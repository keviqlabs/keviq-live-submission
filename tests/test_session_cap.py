"""Per-session spend cap (MODEL_SESSION_CAP_USD, warning MODEL_SESSION_WARN_USD). A session at its cap makes no more
model calls: the refusal is a model_call with ok false in that session, and nothing reaches the transport. Other
sessions still call, the runner keeps going, and with the variables unset nothing changes."""

import json
import logging

import httpx
import pytest

import meta.meter
from app.contracts import make_event, now_ms
from app.main import create_app
from app.store import PgStore
from meta.client import MetaClient
from meta.meter import BudgetExceeded, Meter, memory_spend
from meta.transport import FakeTransport, responses_reply
from worker.runner import Runner

S, T = "pytest-cap-s", "pytest-cap-t"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"label": {"enum": ["order", "other"]}},
    "required": ["label"],
}
OLD_COST_KEYS = {
    "today_usd",
    "total_usd",
    "daily_cap_usd",
    "daily_warn_usd",
    "total_cap_usd",
    "test_sessions",
}


def model_call(sid: str, n: int, usd: float) -> dict:
    return make_event(
        "model_call", sid, 1, "sim", event_id=f"{sid}.mc{n}", model="muse-spark-1.3",
        purpose="test", tokens_in=1, tokens_out=1, cost_usd=usd, latency_ms=1, ok=True,
    )  # fmt: skip


def comment(sid: str, n: int) -> dict:
    return make_event(
        "comment", sid, 1000 + n, "sim", event_id=f"{sid}.c{n}", comment_id=f"c{n}", user_ref="u1",
        text="chốt 1 đôi", reply_to=None,
    )  # fmt: skip


def reply() -> object:
    return responses_reply(json.dumps({"label": "order"}), 10, 2)


def capped(app, cap: float | None = 8.0, warn: float | None = 6.0) -> Meter:
    """The meter the runner builds, its session spend read over the in-process app (C5: cap 8.00, warning 6.00)."""
    spend = meta.meter.http_session_spend("http://test", transport=httpx.ASGITransport(app=app))
    return Meter(memory_spend([]), session_spend=spend, session_cap=cap, session_warn_at=warn)


async def spent(store, sid: str, *costs: float) -> None:
    for n, usd in enumerate(costs):
        await store.append(model_call(sid, n, usd))


async def calls_in(store, sid: str) -> list[dict]:
    return [e for e in await store.read(sid, 0, 1000) if e["type"] == "model_call"]


async def label(event, ctx):
    await ctx.client().responses(input=event["text"], schema=SCHEMA, purpose="label_comments")
    return []


async def test_a_session_at_its_cap_sends_nothing_and_logs_the_refusal_in_that_session(mem_app):
    app, store, client = mem_app
    await spent(store, S, 5.0, 3.0)
    trigger = comment(S, 1)
    assert (await client.post(f"/api/sessions/{S}/events", json=[trigger])).status_code == 200
    transport = FakeTransport([reply()])
    runner = Runner("http://test", {}, http=client)
    runner._meta = (transport, capped(app))

    with pytest.raises(BudgetExceeded) as exc:
        await runner.client_for(trigger).responses(input="x", schema=SCHEMA, purpose="label_comments")
    assert exc.value.reason == "session_cap"
    assert exc.value.spent == pytest.approx(8.0) and exc.value.cap == 8.0
    assert transport.requests == []
    refused = (await calls_in(store, S))[-1]
    assert refused["ok"] is False and refused["error"] == "session_cap" and refused["cost_usd"] == 0
    assert refused["caused_by"] == [trigger["event_id"]]


async def test_another_session_can_still_call(mem_app):
    app, store, client = mem_app
    await spent(store, S, 8.0)
    transport = FakeTransport([reply()])
    runner = Runner("http://test", {}, http=client)
    runner._meta = (transport, capped(app))

    out = await runner.client_for(comment(T, 1)).responses(input="x", schema=SCHEMA, purpose="label_comments")
    assert out.data == {"label": "order"} and len(transport.requests) == 1
    [call] = await calls_in(store, T)
    assert call["ok"] is True and call["session_id"] == T
    with pytest.raises(BudgetExceeded):
        await runner.client_for(comment(S, 1)).responses(input="x", schema=SCHEMA, purpose="label_comments")
    assert len(transport.requests) == 1


async def test_the_warning_fires_once_when_a_session_reaches_the_warning_line(mem_app, caplog):
    app, store, _ = mem_app
    meter = capped(app)
    caplog.set_level(logging.WARNING, logger="meta.meter")

    def warnings(sid: str) -> int:
        return sum(1 for r in caplog.records if r.name == "meta.meter" and sid in r.getMessage())

    await spent(store, S, 5.99)
    await meter.check(S)
    assert warnings(S) == 0
    await store.append(model_call(S, 1, 0.01))  # 6.00
    await meter.check(S)
    await store.append(model_call(S, 2, 1.0))  # 7.00
    await meter.check(S)
    await meter.check(S)
    assert warnings(S) == 1
    await spent(store, T, 6.5)
    await meter.check(T)
    assert warnings(T) == 1 and warnings(S) == 1


async def test_with_the_variables_unset_no_session_spend_is_read(monkeypatch):
    monkeypatch.delenv("MODEL_SESSION_CAP_USD", raising=False)
    monkeypatch.delenv("MODEL_SESSION_WARN_USD", raising=False)
    asked = []

    async def session_spend(sid: str) -> float:
        asked.append(sid)
        return 1000.0

    async def emit(fields):
        ledger.append(fields["cost_usd"])

    ledger = [0.0]
    meter = Meter(memory_spend(ledger), daily_cap=30, total_cap=300, warn_at=20, session_spend=session_spend)
    assert meter.session_cap is None and meter.session_warn_at is None
    transport = FakeTransport([reply()])
    client = MetaClient(transport, meter, emit, session_id=S)
    await client.responses(input="x", schema=SCHEMA, purpose="label_comments")
    assert len(transport.requests) == 1 and asked == []

    ledger.append(30.0)
    with pytest.raises(BudgetExceeded) as exc:
        await client.responses(input="x", schema=SCHEMA, purpose="label_comments")
    assert exc.value.reason == "daily_cap" and asked == [] and len(transport.requests) == 1


async def session_usd_is_that_session_only(client, store, s: str, t: str) -> None:
    await spent(store, s, 0.25, 0.5)
    await spent(store, t, 1.0)
    await spent(store, s + "-2", 2.0)  # an id that starts with s is another session
    body = (await client.get("/api/cost", params={"session": s})).raise_for_status().json()
    assert body["session_usd"] == pytest.approx(0.75)
    assert set(body) == OLD_COST_KEYS | {"session_usd"}
    assert (await client.get("/api/cost", params={"session": t})).json()["session_usd"] == pytest.approx(1.0)
    assert (await client.get("/api/cost", params={"session": s + "-x"})).json()["session_usd"] == 0
    assert set((await client.get("/api/cost")).json()) == OLD_COST_KEYS


async def test_memory_cost_by_session_sums_only_that_session(mem_app):
    _, store, client = mem_app
    await session_usd_is_that_session_only(client, store, S, T)


async def test_pg_cost_by_session_sums_only_that_session(pg_url):
    store = PgStore(pg_url)
    await store.open()
    app = create_app(store)
    app.state.store = store
    stamp = now_ms()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await session_usd_is_that_session_only(
                client, store, f"pytest-cap-s{stamp}", f"pytest-cap-t{stamp}"
            )
    finally:
        await store.close()


async def test_the_runner_skips_the_model_for_a_capped_session_and_keeps_going(mem_app):
    app, store, client = mem_app
    await spent(store, S, 8.0)
    transport = FakeTransport([reply()])
    runner = Runner("http://test", {"comment": [label]}, http=client)
    runner._meta = (transport, capped(app))

    for e in (comment(S, 1), comment(T, 1), comment(S, 2)):
        assert (await client.post(f"/api/sessions/{e['session_id']}/events", json=[e])).status_code == 200
        assert await runner.handle(e) == []
    assert runner.failures == 2 and len(transport.requests) == 1
    assert [c.get("error") for c in await calls_in(store, S)] == [None, "session_cap", "session_cap"]
    assert [c["ok"] for c in await calls_in(store, T)] == [True]


@pytest.mark.parametrize(
    "daily_cap, total_cap, earlier_days, first, at_step, warnings",
    [
        (30.0, 300.0, 0.0, "session_cap", 8, 1),
        (5.0, 300.0, 0.0, "daily_cap", 5, 0),
        (30.0, 300.0, 296.0, "total_cap", 4, 0),
    ],
    ids=["session-first", "daily-first", "total-first"],
)
async def test_with_all_three_caps_set_the_cap_reached_first_is_the_error(
    mem_app, caplog, daily_cap, total_cap, earlier_days, first, at_step, warnings
):
    """Session cap 8.00 (warning 6.00), daily and total caps set too. Each step another 1.00 USD is spent in S, then
    S asks for a call: every call before the first cap goes out (the 6.00 warning does not stop one), the first
    refusal names the cap reached first, and nothing is sent for it."""
    app, store, client = mem_app
    today: list[float] = []

    async def spend() -> tuple[float, float]:
        return sum(today), earlier_days + sum(today)

    session_spend = meta.meter.http_session_spend("http://test", transport=httpx.ASGITransport(app=app))
    meter = Meter(
        spend, daily_cap=daily_cap, total_cap=total_cap, warn_at=20, session_spend=session_spend,
        session_cap=8.0, session_warn_at=6.0,
    )  # fmt: skip
    trigger = comment(S, 1)
    assert (await client.post(f"/api/sessions/{S}/events", json=[trigger])).status_code == 200
    transport = FakeTransport([reply() for _ in range(10)])
    runner = Runner("http://test", {}, http=client)
    runner._meta = (transport, meter)
    caplog.set_level(logging.WARNING, logger="meta.meter")

    refused = None
    for step in range(1, 11):
        await store.append(model_call(S, step, 1.0))
        today.append(1.0)
        try:
            await runner.client_for(trigger).responses(input="x", schema=SCHEMA, purpose="label_comments")
        except BudgetExceeded as exc:
            refused = (step, exc.reason)
            break
    assert refused == (at_step, first)
    assert len(transport.requests) == at_step - 1
    made = [c for c in await calls_in(store, S) if c["purpose"] == "label_comments"]
    assert [c["ok"] for c in made] == [True] * (at_step - 1) + [False]
    assert made[-1]["error"] == first and made[-1]["cost_usd"] == 0
    assert sum(1 for r in caplog.records if r.name == "meta.meter" and S in r.getMessage()) == warnings
