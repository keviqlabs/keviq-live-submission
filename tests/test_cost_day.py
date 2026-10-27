"""The daily cap counts what the store recorded today (VN day), not the ts_ms an event carries. A worker's
model_call takes its trigger's ts_ms, so a call caused by a comment with ts_ms=1 (seq 874) is still money spent
today and must count toward today's cap."""

import pytest

from app import store as store_module
from app.contracts import make_event, now_ms
from app.store import DAY_MS, MemoryStore, PgStore, vn_day_bounds
from meta.meter import BudgetExceeded, Meter
from worker.runner import build


def model_call(sid, n, ts_ms, usd):
    return make_event(
        "model_call", sid, ts_ms, "sim", event_id=f"{sid}.mc{n}", model="muse-spark-1.3",
        purpose="test", tokens_in=1, tokens_out=1, cost_usd=usd, latency_ms=1, ok=True,
    )  # fmt: skip


async def spent(store) -> tuple[float, float]:
    start, end = vn_day_bounds(now_ms())
    return await store.cost_usd(start, end, test_only=True), await store.cost_usd(test_only=True)


async def recorded_today_counts_today(store, monkeypatch, sid):
    now = now_ms()
    before = await spent(store)
    await store.append(model_call(sid, 1, 1, 0.5))  # ts_ms in 1970, recorded now
    await store.append(model_call(sid, 2, now + 400 * DAY_MS, 0.25))  # ts_ms next year, recorded now
    with monkeypatch.context() as m:
        m.setattr(store_module, "now_ms", lambda: now - DAY_MS)
        await store.append(model_call(sid, 3, now, 2.0))  # ts_ms now, recorded yesterday
    after = await spent(store)
    assert after[0] - before[0] == pytest.approx(0.75)
    assert after[1] - before[1] == pytest.approx(2.75)


async def test_memory_today_is_what_the_store_recorded_today(monkeypatch):
    store = MemoryStore()
    await store.open()
    await recorded_today_counts_today(store, monkeypatch, "pytest-day")


async def test_pg_today_is_what_the_store_recorded_today(pg_url, monkeypatch):
    store = PgStore(pg_url)
    await store.open()
    try:
        await recorded_today_counts_today(store, monkeypatch, f"pytest-day-{now_ms()}")
    finally:
        await store.close()


async def test_the_daily_cap_blocks_a_call_whose_trigger_has_an_old_ts_ms(mem_app):
    _, _, client = mem_app
    trigger = make_event(
        "comment", "pytest-pg", 1, "sim", event_id="pytest-pg.c1", comment_id="c1", user_ref="u1",
        text="hi 1", reply_to=None,
    )  # fmt: skip
    call = {
        "type": "model_call", "model": "muse-spark-1.3", "purpose": "label_comments", "tokens_in": 1,
        "tokens_out": 1, "cost_usd": 31.0, "latency_ms": 1, "ok": True, "error": None,
    }  # fmt: skip
    # Posted the way Runner.client_for emits a model_call: the trigger's ts_ms, recorded now.
    for e in (trigger, build(trigger, call, now_ms())):
        assert (await client.post("/api/sessions/pytest-pg/events", json=[e])).status_code == 200

    async def spend():  # meta.meter.http_spend, over the in-process app
        body = (await client.get("/api/cost")).raise_for_status().json()
        return body["today_usd"], body["total_usd"]

    with pytest.raises(BudgetExceeded) as exc:
        await Meter(spend, daily_cap=30, total_cap=300, warn_at=20).check()
    assert exc.value.reason == "daily_cap"
