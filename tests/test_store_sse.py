"""Store, ingest and SSE cursor resume; C7 late comment and quick switch over live-at-t."""

import json

import pytest

from app.contracts import make_event
from app.store import PgStore, live_cards_from
from connectors.sim.replay import load, plan


def comment(sid, n, ts=1):
    return make_event(
        "comment",
        sid,
        ts,
        "sim",
        event_id=f"{sid}.c{n}",
        comment_id=f"c{n}",
        user_ref="u1",
        text=f"hi {n}",
        reply_to=None,
    )


async def sse(client, path, **headers):
    r = await client.get(path, headers=headers)
    assert r.status_code == 200
    return [
        (int(b.split("\n")[0][4:]), json.loads(b.split("data: ", 1)[1]))
        for b in r.text.split("\n\n")
        if b.startswith("id:")
    ]


async def test_ingest_is_idempotent_and_validated(mem_app):
    _, store, client = mem_app
    e = comment("s1", 1)
    a = (await client.post("/api/sessions/s1/events", json=e)).json()["accepted"][0]
    b = (await client.post("/api/sessions/s1/events", json=[e])).json()["accepted"][0]
    assert a["seq"] == b["seq"] and not a["duplicate"] and b["duplicate"]
    assert (
        await client.post("/api/sessions/s1/events", json={**e, "event_id": "x", "extra": 1})
    ).status_code == 422
    assert (await client.post("/api/sessions/s2/events", json={**e, "event_id": "y"})).status_code == 400
    assert await store.head_seq() == 1


async def test_sse_resumes_from_cursor_and_last_event_id(mem_app):
    _, _, client = mem_app
    await client.post("/api/sessions/s1/events", json=[comment("s1", n) for n in range(1, 5)])
    await client.post("/api/sessions/s2/events", json=comment("s2", 9))
    assert [s for s, _ in await sse(client, "/api/sessions/s1/events?follow=false")] == [1, 2, 3, 4]
    assert [s for s, _ in await sse(client, "/api/sessions/s1/events?follow=false&cursor=2")] == [3, 4]
    rows = await sse(client, "/api/sessions/s1/events?follow=false&cursor=1", **{"Last-Event-ID": "3"})
    assert [s for s, _ in rows] == [4]
    everything = await sse(client, "/api/events?follow=false")
    assert {e["session_id"] for _, e in everything} == {"s1", "s2"}
    assert all("ingested_ms" in e and "seq" in e for _, e in everything)


def demo_cards(demo_events):
    return [e for e in demo_events if e["type"] == "card"]


def test_late_comment_pins_the_card_live_at_its_ts(demo_events):
    late = next(e for e in demo_events if e.get("_replay_at_ms") and e["type"] == "comment")
    assert late["_replay_at_ms"] > 24600  # arrives after card B went live
    live = live_cards_from(demo_cards(demo_events), late["ts_ms"])
    assert [c["card_id"] for c in live] == ["card-a"]
    labeled = next(
        e for e in demo_events if e["type"] == "comment_labeled" and e["comment_id"] == late["comment_id"]
    )
    assert labeled["order_draft"]["referenced_card_id"] == "card-a"


def test_quick_switch_retires_the_previous_card(demo_events):
    seen = [e for e in demo_events if e["type"] == "product_seen" and e["category"] == "boot"]
    assert seen[1]["ts_ms"] - seen[0]["ts_ms"] < 3000
    live = live_cards_from(demo_cards(demo_events), seen[1]["ts_ms"] + 5000)
    ids = [c["card_id"] for c in live]
    assert "card-c" not in ids and ids[0] == "card-d"


async def test_state_after_replaying_demo(mem_app, demo_events):
    _, _, client = mem_app
    for _, e in plan(demo_events, "t1", 1_000_000, speed=1000):
        assert (await client.post("/api/sessions/t1/events", json=e)).status_code == 200
    state = (await client.get("/api/sessions/t1/state")).json()
    assert {o["order_id"] for o in state["approval_queue"]} == {"o-05", "o-14"}
    assert len(state["comments"]) == 14


async def test_pg_store_append_only(pg_url):
    store = PgStore(pg_url)
    await store.open()
    try:
        e = comment("pytest-pg", 1)
        e["event_id"] = f"pytest-pg.{e['event_id'].split('.')[-1]}-{e['ts_ms']}-{id(store)}"
        seq, inserted = await store.append(e)
        assert inserted and (await store.append(e)) == (seq, False)
        rows = await store.read("pytest-pg", seq - 1, 10)
        assert rows[0]["event_id"] == e["event_id"]
    finally:
        await store.close()


def test_replay_fixture_switch(demo_events, tmp_path):
    f = tmp_path / "e.jsonl"
    f.write_text("".join(json.dumps(e) + "\n" for e in demo_events))
    assert not any(e["event_id"].startswith("fx-") for e in load(f, fixtures=False))
    assert len(load(f)) == len(demo_events)


@pytest.mark.parametrize("speed", [1, 4])
def test_replay_plan_rebases_time_and_ids(demo_events, speed):
    sched = plan(demo_events, "demo1-x", 10_000_000, speed)
    by_id = {e["event_id"]: (t, e) for t, e in sched}
    t, late = by_id["demo1-x.cm-07"]
    assert late["ts_ms"] == 10_000_000 + 15000 // speed and t == 10_000_000 + 30000 // speed
    assert "_replay_at_ms" not in late
    card = by_id["demo1-x.fx-card-a2"][1]
    assert card["session_id"] == "demo1-x" and card["provenance"]["speech_event_ids"] == ["demo1-x.sp-a1"]
    assert card["caused_by"] == ["demo1-x.sp-a1"]
    assert [t for t, _ in sched] == sorted(t for t, _ in sched)
