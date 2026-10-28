"""meta/: daily cap stops calls and is logged, 429 backoff honours Retry-After, schema retry then SchemaError (C7)."""

import asyncio
import json

import pytest

from meta.client import MetaClient, ModelError, SchemaError
from meta.meter import BudgetExceeded, Meter, memory_spend
from meta.pricing import cost_usd
from meta.transport import FakeTransport, Reply, responses_reply

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"label": {"enum": ["order", "other"]}},
    "required": ["label"],
}


def make(replies, spent=0.0, daily_cap=30.0):
    calls, sleeps = [], []
    ledger = [spent]

    async def emit(fields):
        calls.append(fields)
        ledger.append(fields["cost_usd"])

    async def sleep(s):
        sleeps.append(s)

    meter = Meter(memory_spend(ledger), daily_cap=daily_cap, total_cap=300, warn_at=20)
    transport = FakeTransport(replies)
    return MetaClient(transport, meter, emit, sleep=sleep), transport, calls, sleeps


def test_prices():
    assert cost_usd("muse-spark-1.3", 1_000_000, 1_000_000) == pytest.approx(1.25 + 4.25)
    assert cost_usd("muse-spark-1.3", 1_000_000, 0, cached_in=1_000_000) == pytest.approx(0.15)
    assert cost_usd("muse-voice-transcribe-1.0", units={"audio_seconds": 3600.9}) == pytest.approx(0.18)
    assert cost_usd("sam-3.1", units={"images": 1000}) == pytest.approx(2.5)
    assert cost_usd("sam-3.1", units={"frames": 10}) == pytest.approx(50 * 0.2 / 1000)
    assert cost_usd("muse-image-1.0", units={"images": 3}) == pytest.approx(0.03)


async def test_structured_output_is_parsed_and_logged():
    client, transport, calls, _ = make([responses_reply(json.dumps({"label": "order"}), 1000, 10)])
    out = await client.responses(input="Chốt 1 đôi", schema=SCHEMA, purpose="label_comments")
    assert out.data == {"label": "order"}
    body = transport.requests[0][1]
    assert body["text"]["format"]["type"] == "json_schema" and body["text"]["format"]["strict"] is True
    assert calls[0]["ok"] and calls[0]["tokens_in"] == 1000 and calls[0]["cost_usd"] > 0


async def test_invalid_schema_output_is_retried_once_then_raises():
    bad = responses_reply(json.dumps({"label": "maybe"}))
    client, transport, calls, _ = make([bad, responses_reply("not json")])
    with pytest.raises(SchemaError):
        await client.responses(input="x", schema=SCHEMA, purpose="label_comments")
    assert len(transport.requests) == 2 and len(calls) == 2


async def test_invalid_then_valid_succeeds_on_retry():
    client, _, _, _ = make([responses_reply("{}"), responses_reply(json.dumps({"label": "other"}))])
    assert (await client.responses(input="x", schema=SCHEMA, purpose="p")).data == {"label": "other"}


async def test_429_backs_off_with_retry_after_then_succeeds():
    replies = [
        Reply(429, {"error": "rate"}, {"retry-after": "2"}),
        Reply(503, {}, {}),
        responses_reply(json.dumps({"label": "other"})),
    ]
    client, transport, calls, sleeps = make(replies)
    await client.responses(input="x", schema=SCHEMA, purpose="p")
    assert sleeps == [2.0, 1.0] and len(transport.requests) == 3
    assert [c["error"] for c in calls] == ["http_429", "http_503", None]


async def test_gives_up_after_four_attempts_and_does_not_retry_400():
    client, transport, _, sleeps = make([Reply(429, {}, {})] * 4)
    with pytest.raises(ModelError):
        await client.responses(input="x", schema=SCHEMA, purpose="p")
    assert len(transport.requests) == 4 and len(sleeps) == 3
    client, transport, _, _ = make([Reply(400, {"error": "bad"}, {})])
    with pytest.raises(ModelError):
        await client.responses(input="x", schema=SCHEMA, purpose="p")
    assert len(transport.requests) == 1


async def test_daily_cap_stops_the_call_and_logs_it():
    client, transport, calls, _ = make([responses_reply("{}")], spent=30.0)
    with pytest.raises(BudgetExceeded) as exc:
        await client.responses(input="x", schema=SCHEMA, purpose="p")
    assert exc.value.reason == "daily_cap"
    assert transport.requests == []
    assert calls == [{**calls[0], "ok": False, "error": "daily_cap", "cost_usd": 0.0}]


async def test_blocked_model_is_refused():
    client, transport, _, _ = make([])
    with pytest.raises(ModelError):
        await client.responses(input="x", schema=SCHEMA, purpose="p", model="muse-wonder-1.0")
    assert transport.requests == []


async def test_sam_and_image_bill_per_image():
    client, transport, calls, _ = make([responses_reply("[]"), Reply(200, {"data": [{"url": "u"}]}, {})])
    await client.sam("data:image/jpeg;base64,AAAA", "white sneaker")
    await client.image("x")
    assert calls[0]["units"] == {"images": 1} and calls[0]["cost_usd"] == pytest.approx(0.0025)
    assert transport.requests[1][0] == "/images/generations" and calls[1]["cost_usd"] == pytest.approx(0.01)


async def test_a_call_with_no_reply_is_still_logged_and_not_retried():
    """Cancelled (a worker lane's time limit) or no connection: a model_call all the same (AGENTS.md rule 5), not
    ok, cost 0 since no usage came back, and the error goes up to the caller."""

    class Hangs(FakeTransport):
        async def post(self, path, body):
            self.requests.append((path, body))
            await asyncio.Event().wait()

    class Drops(FakeTransport):
        async def post(self, path, body):
            self.requests.append((path, body))
            raise OSError("connection reset")

    client, _, calls, _ = make([])
    client.transport = Hangs([])
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(client.responses(input="x", schema=SCHEMA, purpose="label_comments"), 0.05)
    client.transport = Drops([])
    with pytest.raises(OSError):
        await client.responses(input="x", schema=SCHEMA, purpose="label_comments")
    assert len(client.transport.requests) == 1
    assert [(c["ok"], c["error"], c["cost_usd"]) for c in calls] == [
        (False, "cancelled", 0.0),
        (False, "transport_OSError", 0.0),
    ]
