"""A store call cancelled mid-query (an SSE client that disconnects) must leave PgStore usable."""

import asyncio
import time

import anyio
import psycopg
import pytest

from app.contracts import make_event
from app.store import PgStore

SID = "pytest-cancel"


def comment(n: str):
    return make_event(
        "comment",
        SID,
        1,
        "sim",
        event_id=f"{SID}.{n}",
        comment_id=n,
        user_ref="u1",
        text="hi",
        reply_to=None,
    )


async def cancel_while_events_locked(url: str, call, how: str, while_locked=None) -> None:
    """Hold an ACCESS EXCLUSIVE lock on events from another connection so `call` hangs, then cancel it.

    `while_locked(lk)` runs after the cancel, before the lock is released."""
    lk = await psycopg.AsyncConnection.connect(url)  # the lock lives in this connection's open transaction
    try:
        await lk.execute("LOCK TABLE events IN ACCESS EXCLUSIVE MODE")
        if how == "anyio":  # Starlette's StreamingResponse cancels the stream like this on disconnect
            with anyio.move_on_after(0.5) as scope:
                await call()
            assert scope.cancelled_caught
        else:
            t = asyncio.create_task(call())
            await asyncio.sleep(0.5)
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
        if while_locked:
            await while_locked(lk)
    finally:
        await lk.rollback()
        await lk.close()


@pytest.fixture
async def store(pg_url):
    s = PgStore(pg_url)
    await s.open()
    yield s
    await s.close()


@pytest.mark.parametrize("how", ["anyio", "task"])
async def test_cancelled_read_leaves_the_store_usable(pg_url, store, how):
    await cancel_while_events_locked(pg_url, lambda: store.read(SID, 0, 10), how)
    assert await store.head_seq() >= 0
    assert isinstance(await store.read(SID, 0, 10), list)


@pytest.mark.parametrize("how", ["anyio", "task"])
async def test_cancelled_append_saves_nothing_and_the_next_append_works(pg_url, store, how):
    e = comment(f"c{time.time_ns()}")
    await cancel_while_events_locked(pg_url, lambda: store.append(e), how)
    async with await psycopg.AsyncConnection.connect(pg_url) as other:
        cur = await other.execute("SELECT COUNT(*) FROM events WHERE event_id = %s", (e["event_id"],))
        assert (await cur.fetchone())[0] == 0
    seq, new = await store.append(e)
    assert new and seq > 0
    assert (await store.read(SID, seq - 1, 1))[0]["event_id"] == e["event_id"]


async def lock_waiters(lk) -> int:
    cur = await lk.execute(
        "SELECT COUNT(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND datname = current_database()"
    )
    return (await cur.fetchone())[0]


@pytest.mark.parametrize("how", ["anyio", "task"])
async def test_cancelled_query_does_not_keep_waiting_on_the_server(pg_url, store, how):
    async def next_call_then_count(lk):
        await store.card_versions(SID)  # cards, not events: runs while events is still locked
        for _ in range(20):
            if await lock_waiters(lk) == 0:
                return
            await asyncio.sleep(0.1)
        pytest.fail("the cancelled query still waits for the events lock on the server")

    await cancel_while_events_locked(pg_url, lambda: store.read(SID, 0, 10), how, next_call_then_count)
