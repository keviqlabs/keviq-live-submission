"""worker/brain/card.py: product_seen opens a draft, a spoken price makes it live, a switch never merges (C2, C4, C7)."""

import asyncio

import pytest

from app.contracts import make_event, now_ms
from measure.core import c1
from meta.meter import Meter, memory_spend
from meta.transport import FakeTransport, Reply, responses_reply
from worker.brain import card, window
from worker.runner import Runner
from worker.vision import frames, sam
from worker.voice import vn

SID = "cards1"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("CARDS_FROM_REPLAY", raising=False)
    monkeypatch.setattr(window, "HOLD_MS", 0)  # no product_seen is still being read unless a test says so
    card.SESSIONS.clear()
    frames._CACHE.clear()
    yield
    card.SESSIONS.clear()
    frames._CACHE.clear()


def runner_with(client, replies: list[Reply]) -> Runner:
    runner = Runner("http://test", card.HANDLERS, http=client)
    runner._meta = (FakeTransport(replies), Meter(memory_spend([])))
    return runner


def seen(event_id: str, category: str, color: str | None, ts: int, caused_by: list[str] | None = ("fr-1",)):
    extra = {"caused_by": list(caused_by)} if caused_by else {}
    return make_event(
        "product_seen", SID, ts, "replay", event_id=event_id, category=category,
        attributes={"color": color, "size": None, "material": None}, confidence=0.9, is_new=True,
        frame_ts_ms=ts, **extra,
    )  # fmt: skip


def said(event_id: str, text: str, ts: int):
    return make_event(
        "speech",
        SID,
        ts,
        "replay",
        event_id=event_id,
        text=text,
        final=True,
        lang="vi",
        start_ms=ts,
        end_ms=ts,
    )


async def run(client, runner: Runner, event: dict) -> list[dict]:
    assert (await client.post(f"/api/sessions/{SID}/events", json=[event])).status_code == 200
    return await runner.handle(event)


async def cards_in(store) -> list[dict]:
    return [e for e in await store.read(SID, 0, 500) if e["type"] == "card"]


async def settle() -> None:
    while (s := card.SESSIONS.get(SID)) and s.tasks:
        await asyncio.gather(*list(s.tasks))


async def hear(client, store, runner: Runner, event: dict) -> list[dict]:
    """The handlers post their own versions (speech after a background read) and return nothing; the cards come
    from the store once the reads are done."""
    assert await run(client, runner, event) == []
    await settle()
    return [c for c in await cards_in(store) if (c.get("caused_by") or [None])[0] == event["event_id"]]


async def test_new_product_opens_a_draft_from_the_catalog(mem_app):
    _, store, client = mem_app
    ts = now_ms()
    [out] = await hear(client, store, runner_with(client, []), seen("ps-a", "sneaker", "trắng", ts))
    assert out["card_id"] == "card-a" and out["version"] == 1 and out["status"] == "draft"
    assert out["sku"] == "GIAY-01" and out["price"] is None and out["stock"] == 6
    assert out["caused_by"] == ["ps-a"]
    assert set(out["timing"]) == {"received_ms", "infer_start_ms", "infer_end_ms", "emitted_ms"}
    events = await store.read(SID, 0, 100)
    assert c1(events)["covered"] == 1


async def test_unknown_item_gets_a_named_draft_without_sku(mem_app):
    _, store, client = mem_app
    [out] = await hear(
        client, store, runner_with(client, []), seen("ps-x", "sneaker", None, now_ms())
    )  # two sneakers
    assert out["sku"] is None and out["name"] == "Giày sneaker" and out["variants"] == []


async def test_replayed_product_seen_without_a_cause_needs_the_flag(mem_app, monkeypatch):
    _, store, client = mem_app
    runner = runner_with(client, [])
    assert await hear(client, store, runner, seen("ps-r", "boot", "đen", now_ms(), caused_by=None)) == []
    monkeypatch.setenv("CARDS_FROM_REPLAY", "1")
    [out] = await hear(client, store, runner, seen("ps-r2", "boot", "đen", now_ms(), caused_by=None))
    assert out["sku"] == "BOOT-02"


async def test_quick_switch_retires_the_unpriced_draft_instead_of_merging(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, [])
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    retired, opened = await hear(client, store, runner, seen("ps-b", "bag", "đỏ", ts + 2000))
    assert (retired["card_id"], retired["version"], retired["status"]) == ("card-a", 2, "retired")
    assert (opened["card_id"], opened["sku"], opened["status"]) == ("card-b", "TUI-03", "draft")
    assert all(e["caused_by"][0] == "ps-b" for e in (retired, opened))
    assert len(await cards_in(store)) == 3


async def test_same_product_again_reopens_its_card(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, [Reply(400, {"error": "bad"})])
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts + 500))
    await run(client, runner, seen("ps-b", "bag", "đỏ", ts + 4000))
    retired, back = await hear(client, store, runner, seen("ps-c", "sneaker", "trắng", ts + 9000))
    assert (retired["card_id"], retired["status"]) == ("card-b", "retired")
    assert (back["card_id"], back["status"], back["price"]) == ("card-a", "live", 450000)
    assert back["provenance"]["frame_ts_ms"] == ts + 9000


async def test_spark_reads_price_and_sizes_and_the_card_goes_live(mem_app):
    _, store, client = mem_app
    reply = responses_reply('{"price_vnd": 450000, "sizes": ["38", "39"], "colors": []}')
    runner = runner_with(client, [reply])
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    [live] = await hear(
        client, store, runner, said("sp-1", "đôi này bốn trăm rưỡi, còn size 38 với 39", ts + 800)
    )
    assert (live["version"], live["status"], live["price"]) == (2, "live", 450000)
    assert [v["size"] for v in live["variants"]] == ["38", "39"] and live["stock"] == 4
    assert live["provenance"]["speech_event_ids"] == ["sp-1"] and live["caused_by"][0] == "sp-1"
    calls = [e for e in await store.read(SID, 0, 100) if e["type"] == "model_call"]
    assert [c["purpose"] for c in calls] == ["card_from_speech"] and calls[0]["caused_by"] == ["sp-1"]


async def test_rule_reader_takes_over_when_the_model_fails(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, [Reply(400, {"error": "bad"})])
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    [live] = await hear(client, store, runner, said("sp-1", "đôi này giá 1tr2 nha các chị", ts + 800))
    assert (live["status"], live["price"]) == ("live", 1_200_000)


async def test_speech_without_price_or_card_changes_nothing(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, [Reply(400, {}), Reply(400, {})])
    ts = now_ms()
    assert (
        await hear(client, store, runner, said("sp-0", "chào cả nhà", ts)) == []
    )  # no card yet: no model call
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    assert await hear(client, store, runner, said("sp-1", "chào cả nhà", ts + 500)) == []
    assert not [
        e for e in await store.read(SID, 0, 100) if e["type"] == "model_call" and e["caused_by"] == ["sp-0"]
    ]


async def test_live_card_gets_the_sam_cut_out_as_its_next_version(mem_app, monkeypatch):
    _, store, client = mem_app
    runner = runner_with(client, [responses_reply('{"price_vnd": 450000, "sizes": [], "colors": []}')])
    ts = now_ms()
    frames.put(SID, ts, b"\xff\xd8jpeg")

    async def fake_cut_out(client, jpeg, category):
        assert jpeg == b"\xff\xd8jpeg" and category == "sneaker"
        return "data:image/png;base64,iVBORw0KGgo=", (ts + 900, ts + 1500)

    monkeypatch.setattr(sam, "cut_out", fake_cut_out)
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts, caused_by=["fr-a"]))
    await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts + 800))
    last = (await cards_in(store))[-1]
    assert (last["version"], last["status"]) == (3, "live")
    assert last["image_url"].startswith("data:image/png") and last["caused_by"] == ["sp-1", "fr-a"]
    assert last["timing"]["infer_end_ms"] - last["timing"]["infer_start_ms"] == 600


async def test_a_slow_cut_out_does_not_hold_up_the_next_line(mem_app, monkeypatch):
    """SAM takes its time on the card that just went live; the next line is decided meanwhile, and the cut-out
    still arrives as a later version."""
    _, store, client = mem_app
    runner = runner_with(client, [])
    ts, gate = now_ms(), asyncio.Event()
    frames.put(SID, ts, b"\xff\xd8jpeg")

    async def read(text, ctx):
        return vn.Spoken(450000 if "450" in text else None, ["38"] if "38" in text else [], []), (0, 0)

    async def slow_cut_out(client, jpeg, category):
        await gate.wait()
        return "data:image/png;base64,iVBORw0KGgo=", (ts, ts)

    monkeypatch.setattr(card, "read_speech", read)
    monkeypatch.setattr(sam, "cut_out", slow_cut_out)
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts, caused_by=["fr-a"]))
    await run(client, runner, said("sp-1", "đôi này 450 nghìn", ts + 800))
    await run(client, runner, said("sp-2", "size 38 nhé", ts + 1600))
    for _ in range(100):
        sized = [c for c in await cards_in(store) if c["caused_by"][0] == "sp-2"]
        if sized:
            break
        await asyncio.sleep(0.01)
    gate.set()
    await settle()
    assert sized and [v["size"] for v in sized[0]["variants"]] == ["38"] and not sized[0]["image_url"]
    last = (await cards_in(store))[-1]
    assert last["image_url"].startswith("data:image/png") and last["version"] > sized[0]["version"]


async def test_console_edit_is_followed_so_versions_keep_counting(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, [Reply(400, {})])
    ts = now_ms()
    [draft] = await hear(client, store, runner, seen("ps-a", "sneaker", "trắng", ts))
    edit = {k: draft[k] for k in card.CARD_FIELDS} | {"version": 2, "price": 399000, "status": "edited"}
    await run(client, runner, make_event("card", SID, ts + 100, "replay", event_id="console-1", **edit))
    [nxt] = await hear(client, store, runner, said("sp-1", "size 38 nhé", ts + 200))
    assert nxt["version"] == 3 and nxt["price"] == 399000 and [v["size"] for v in nxt["variants"]] == ["38"]


async def test_a_slow_read_does_not_hold_up_the_next_product(mem_app, monkeypatch):
    _, store, client = mem_app
    runner = runner_with(client, [])
    gate = asyncio.Event()

    async def slow_read(text, ctx):
        await gate.wait()
        return vn.Spoken(450000 if "450" in text else None, [], []), (0, 0)

    monkeypatch.setattr(card, "read_speech", slow_read)
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    assert await run(client, runner, said("sp-1", "đôi này 450 nghìn", ts + 500)) == []
    assert await run(client, runner, seen("ps-b", "bag", "đỏ", ts + 1000)) == []  # card-a is not retired
    [opened] = [c for c in await cards_in(store) if c["caused_by"][0] == "ps-b"]  # sp-1 is still being read
    assert (opened["card_id"], opened["status"]) == ("card-b", "draft")
    assert await run(client, runner, said("sp-2", "túi này đẹp lắm", ts + 1500)) == []
    await run(client, runner, seen("ps-c", "boot", "đen", ts + 2000))
    gate.set()
    await settle()
    latest = {c["card_id"]: c for c in await cards_in(store)}
    assert (latest["card-a"]["status"], latest["card-a"]["price"]) == ("live", 450000)
    assert latest["card-a"]["caused_by"][0] == "sp-1"
    assert latest["card-b"]["status"] == "retired" and latest["card-b"]["caused_by"][0] == "sp-2"
    assert latest["card-c"]["status"] == "draft"


async def test_a_product_seen_handled_twice_is_one_card_version(mem_app):
    """The runner's fast lane may get the same product_seen again (a stream resumed at an older cursor)."""
    _, store, client = mem_app
    runner = runner_with(client, [])
    event = seen("ps-a", "sneaker", "trắng", now_ms())
    await run(client, runner, event)
    assert await runner.handle(event) == []
    await settle()
    assert [(c["card_id"], c["version"]) for c in await cards_in(store)] == [("card-a", 1)]


async def test_an_older_frame_product_seen_is_stale(mem_app):
    """A product_seen of a frame older than the last one handled arrives late: it opens no card."""
    _, store, client = mem_app
    runner = runner_with(client, [])
    t = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", t))
    await run(client, runner, seen("ps-b", "bag", None, t - 5000))
    await settle()
    assert [(c["card_id"], c["version"]) for c in await cards_in(store)] == [("card-a", 1)]


async def test_speech_lines_are_read_a_few_at_a_time_and_decided_in_arrival_order(mem_app, monkeypatch):
    """A burst of lines never starts a read per line at once (no unbounded tasks); the later ones wait their turn,
    and a line read faster than the one before it still waits for that one's decision."""
    _, store, client = mem_app
    runner = runner_with(client, [])
    reading, most, decided = 0, 0, []

    async def slow_read(text, ctx):
        nonlocal reading, most
        reading += 1
        most = max(most, reading)
        await asyncio.sleep(0.01 * (10 - int(text.split()[-1])))  # later lines read faster
        reading -= 1
        return vn.Spoken(None, [], []), (0, 0)

    decide = card.decide

    async def logged(event, *args):
        decided.append(event["event_id"])
        await decide(event, *args)

    monkeypatch.setattr(card, "read_speech", slow_read)
    monkeypatch.setattr(card, "decide", logged)
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    for n in range(8):
        assert await run(client, runner, said(f"sp-{n}", f"câu {n}", ts + 100 * n)) == []
    assert len(card.SESSIONS[SID].lines) == 8  # every line is known to the cards while it waits
    await settle()
    assert most == 4  # card.SPEECH_READS
    assert decided == [f"sp-{n}" for n in range(8)]
