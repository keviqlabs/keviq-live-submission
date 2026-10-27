"""worker/: trigger first in caused_by, timing stamped, a failing handler does not stop the others (C1, C6, C7)."""

import json

from measure.core import c1, latency
from worker.runner import Runner, parse_sse

ENVELOPE = {"event_id", "session_id", "ts_ms", "source", "v", "caused_by", "timing"}


def partial_card(demo_events: list[dict]) -> dict:
    card = next(e for e in demo_events if e["type"] == "card" and "ps-a" in (e.get("caused_by") or []))
    return {k: v for k, v in card.items() if k not in ENVELOPE}


async def test_handler_output_reaches_the_store_with_provenance_and_timing(mem_app, demo_events):
    _, store, client = mem_app
    trigger = next(e for e in demo_events if e["event_id"] == "ps-a")
    assert (await client.post("/api/sessions/demo1/events", json=[trigger])).status_code == 200

    async def make_card(event, ctx):
        return [
            {
                **partial_card(demo_events),
                "caused_by": ["sp-a1"],
                "_infer": (ctx.received_ms, ctx.received_ms + 7),
            }
        ]

    runner = Runner("http://test", {"product_seen": [make_card]}, http=client)
    [out] = await runner.handle(trigger)
    assert out["caused_by"] == ["ps-a", "sp-a1"]
    assert set(out["timing"]) == {"received_ms", "infer_start_ms", "infer_end_ms", "emitted_ms"}

    events = await store.read("demo1", 0, 100)
    assert [e["type"] for e in events] == ["product_seen", "card"]
    assert c1(events) == {"n": 1, "covered": 1, "missing": [], "cycle_ms": 5000}
    assert latency(events)["card"]["infer"]["p50"] == 7


async def test_failing_handler_is_isolated(mem_app, demo_events):
    _, store, client = mem_app
    trigger = next(e for e in demo_events if e["event_id"] == "ps-a")
    await client.post("/api/sessions/demo1/events", json=[trigger])

    async def boom(event, ctx):
        raise RuntimeError("model timed out")

    async def ok(event, ctx):
        return [partial_card(demo_events)]

    runner = Runner("http://test", {"product_seen": [boom, ok]}, http=client)
    out = await runner.handle(trigger)
    assert len(out) == 1 and runner.failures == 1
    assert await runner.handle({**trigger, "type": "speech"}) == []
    assert len(await store.read("demo1", 0, 100)) == 2


async def test_unhandled_trigger_without_consequence_is_reported_missing(mem_app, demo_events):
    _, store, client = mem_app
    trigger = next(e for e in demo_events if e["event_id"] == "ps-a")
    await client.post("/api/sessions/demo1/events", json=[trigger])
    await Runner("http://test", {}, http=client).handle(trigger)
    assert c1(await store.read("demo1", 0, 100))["missing"] == ["ps-a"]


async def test_parse_sse():
    a, b = {"event_id": "a"}, {"event_id": "b"}

    async def lines():
        for line in [
            ": keepalive",
            "",
            "id: 3",
            f"data: {json.dumps(a)}",
            "",
            "id: 9",
            f"data: {json.dumps(b)}",
            "",
        ]:
            yield line

    assert [x async for x in parse_sse(lines())] == [(3, a), (9, b)]
