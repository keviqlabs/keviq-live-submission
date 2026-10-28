"""/api/cost reads its numbers in one go: a model_call recorded while the request runs is in all of them or in
none, so today_usd, total_usd, the test-session part and session_usd agree with each other."""

from contextlib import asynccontextmanager

import httpx

from app import store as store_module
from app.contracts import make_event, now_ms
from app.main import create_app
from app.store import DAY_MS, PgStore


def model_call(sid, n, usd):
    return make_event(
        "model_call", sid, 1, "sim", event_id=f"{sid}.mc{n}", model="muse-spark-1.3",
        purpose="test", tokens_in=1, tokens_out=1, cost_usd=usd, latency_ms=1, ok=True,
    )  # fmt: skip


def numbers(body: dict) -> dict:
    t = body["test_sessions"]
    return {"today": body["today_usd"], "total": body["total_usd"], "test_today": t["today_usd"],
            "test_total": t["total_usd"], "session": body["session_usd"]}  # fmt: skip


@asynccontextmanager
async def client_over(store):
    app = create_app(store)
    app.state.store = store  # ASGITransport skips lifespan
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def cost(client, sid) -> dict:
    return numbers((await client.get("/api/cost", params={"session": sid})).raise_for_status().json())


async def test_a_call_recorded_during_the_request_is_in_every_number_or_in_none(pg_url, monkeypatch):
    sid = f"pytest-one-read-{now_ms()}"
    store = PgStore(pg_url)
    await store.open()
    fetch, landed = store._fetch, []

    async def a_call_lands_after_the_first_read(sql, params=()):
        rows = await fetch(sql, params)
        if "model_call" in sql and not landed:
            landed.append(await store.append(model_call(sid, 1, 0.004)))
        return rows

    try:
        async with client_over(store) as client:
            monkeypatch.setattr(store, "_fetch", a_call_lands_after_the_first_read)
            during = await cost(client, sid)
            monkeypatch.setattr(store, "_fetch", fetch)
            after = await cost(client, sid)
    finally:
        await store.close()
    assert landed
    missed = round(after["session"] - during["session"], 6)  # 0.004 if the request read first, else 0
    assert {k: round(after[k] - during[k], 6) for k in after} == {
        "today": missed, "total": missed, "test_today": missed, "test_total": missed, "session": missed}  # fmt: skip


async def what_each_number_counts(store, client, monkeypatch, sid):
    """Today is what the store recorded today; total is all time; the test part is test sessions only; session_usd
    is that session, all time."""
    other = f"one-read-{now_ms()}"  # not a test session
    before = await cost(client, sid)
    now = now_ms()
    await store.append(model_call(sid, 1, 0.004))
    await store.append(model_call(other, 1, 0.001))
    with monkeypatch.context() as m:
        m.setattr(store_module, "now_ms", lambda: now - DAY_MS)
        await store.append(model_call(sid, 2, 0.008))  # recorded yesterday
    after = await cost(client, sid)
    assert {k: round(after[k] - before[k], 6) for k in after} == {
        "today": 0.005, "total": 0.013, "test_today": 0.004, "test_total": 0.012, "session": 0.012}  # fmt: skip


async def test_memory_each_number_counts_what_it_says(mem_app, monkeypatch):
    _, store, client = mem_app
    await what_each_number_counts(store, client, monkeypatch, "pytest-one-read")


async def test_pg_each_number_counts_what_it_says(pg_url, monkeypatch):
    store = PgStore(pg_url)
    await store.open()
    try:
        async with client_over(store) as client:
            await what_each_number_counts(store, client, monkeypatch, f"pytest-one-read-{now_ms()}")
    finally:
        await store.close()
