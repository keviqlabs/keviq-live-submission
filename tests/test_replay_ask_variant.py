"""data/sessions/ask-variant replayed as the worker sees it: the orders worker over the app (MemoryStore) leaves the
orders and messages gold.json expects (asked, not guessed; the wrong customer changes nothing), and the labels
worker leaves the session to its hand-written labels, so the replay calls no model."""

import json

from app.contracts import now_ms
from app.settings import ROOT
from connectors.sim import replay
from measure.core import c1
from orders import handlers
from worker.brain.labels import Labeler
from worker.runner import Runner

SESSION = ROOT / "data" / "sessions" / "ask-variant"
SID = "rp1"
EXPECTED = json.loads((SESSION / "gold.json").read_text())["expected"]


def planned() -> list[dict]:
    """The session as `make replay SESSION=ask-variant` posts it (with fixtures), started 2 min ago."""
    return [
        e for _, e in replay.plan(replay.load(SESSION / "events.jsonl"), SID, start_ms=now_ms() - 120_000)
    ]


async def test_orders_and_messages_match_gold(mem_app):
    _, store, client = mem_app
    runner = Runner("http://test", handlers.HANDLERS, http=client)
    for e in planned():
        assert (await client.post(f"/api/sessions/{SID}/events", json=[e])).status_code == 200
        await runner.handle(e)
    assert runner.failures == 0
    events = await store.read(SID, 0, 2000)

    last = {e["order_id"]: e for e in events if e["type"] == "order"}
    got = {oid: {"state": o["state"], "variant": o["lines"][0]["variant"]} for oid, o in last.items()}
    assert got == EXPECTED["orders"]
    out = [e for e in events if e["type"] == "message_out"]
    threads = EXPECTED["messages_out"]
    assert {t: [m["template"] for m in out if m["thread_ref"] == t] for t in threads} == threads
    assert {m["thread_ref"] for m in out} <= set(threads)

    # The one CONFIRMED order follows its own customer's confirmation, in that customer's thread.
    by_id = {e["event_id"]: e for e in events}
    [confirmed] = [e for e in events if e["type"] == "order" and e["state"] == "CONFIRMED"]
    [reply] = [by_id[c] for c in confirmed["caused_by"] if by_id[c]["type"] == "message_in"]
    assert (reply["thread_ref"], reply["template"], reply["payload"]["order_id"]) == (
        "th-u064",
        "confirm_yes",
        "o-c019",
    )
    assert c1(events)["missing"] == []


async def test_the_labels_worker_leaves_the_hand_written_labels_alone():
    def no_model(ctx):
        raise AssertionError("the replay must not call the model")

    labeler = Labeler(client_for=no_model)
    for e in planned():
        for fn in labeler.handlers().get(e["type"], []):
            assert await fn(e, None) == []
    assert not labeler.queues[SID] and not labeler.tasks
