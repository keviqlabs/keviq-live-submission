"""Real app + test PostgreSQL + runner, with synthetic slow vision and no model transport.

See docs/specs/slow-vision-integration.md for clocks, transport limitations and deferred binding cases.
"""

import asyncio
import contextlib
import contextvars
import json
import os
import time
import uuid
import weakref
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app.contracts import make_event, now_ms
from app.main import create_app
from app.settings import ROOT, is_test_database
from app.store import PgStore
from connectors.sim.moderator import SimModerator
from measure import c5_live, c5_progress
from measure.c5_shop import Feed
from tests.db_lock import locked_test_database
from worker import runner as runner_mod
from worker.brain import labels
from worker.runner import Runner

LOOP_MS = 351500
SPEED = float(os.environ.get("SLOW_VISION_SPEED", "20"))
OUTCOMES = ("done", "skipped", "stale", "failed")
SERVER_CONTEXT = contextvars.ContextVar("test_asgi_server", default=False)
SERVER_TASKS = weakref.WeakSet()


class Infrastructure(set):
    """The in-process app's tasks belong to the server, not to the measured worker process."""

    def __contains__(self, task):
        return super().__contains__(task) or task in SERVER_TASKS


class NoModel:
    async def responses(self, *args, **kwargs):
        raise RuntimeError("model transport disabled in offline integration")


class AppClient(httpx.AsyncClient):
    """Real ASGI routes with bounded SSE reads: ASGITransport buffers a response until it ends."""

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        if method == "GET" and str(url).endswith("/events"):
            kwargs["params"] = dict(kwargs.get("params") or {}) | {"follow": "false"}
            await asyncio.sleep(0.005)
        async with super().stream(method, url, **kwargs) as response:
            yield response


@pytest.fixture
async def pg_app(pg_url):
    import psycopg
    from psycopg.rows import dict_row

    assert is_test_database(pg_url) and not os.environ.get("MODEL_API_KEY")
    with locked_test_database():
        store, app, guard, guard_conn = PgStore(pg_url), None, None, None
        loop = asyncio.get_running_loop()
        prior_factory = loop.get_task_factory()
        factory_installed = False
        try:
            guard_conn = await psycopg.AsyncConnection.connect(pg_url, autocommit=True, row_factory=dict_row)
            cursor = await guard_conn.execute(
                "SELECT pg_try_advisory_lock(hashtext('keviq_test')) AS locked, pg_backend_pid() AS backend_pid"
            )
            rows = await cursor.fetchall()
            if not rows[0]["locked"]:
                pytest.fail("keviq_test advisory lock is held by another run")
            guard = rows[0]["backend_pid"]
            await store.open()
            app = create_app(store)
            app.state.store = store
            await app.state.attachments.start()
            SERVER_TASKS.add(app.state.attachments.task)

            def task_factory(loop, coro, context=None):
                task = (
                    prior_factory(loop, coro, context=context)
                    if prior_factory
                    else asyncio.Task(coro, loop=loop, context=context)
                )
                if context.get(SERVER_CONTEXT, False) if context is not None else SERVER_CONTEXT.get():
                    SERVER_TASKS.add(task)
                return task

            async def server(scope, receive, send):
                token = SERVER_CONTEXT.set(True)
                try:
                    await app(scope, receive, send)
                finally:
                    SERVER_CONTEXT.reset(token)

            loop.set_task_factory(task_factory)
            factory_installed = True
            sid = "slow-vision-" + uuid.uuid4().hex[:12]
            async with AppClient(transport=httpx.ASGITransport(app=server), base_url="http://test") as client:
                client.app = app
                yield store, client, sid
        finally:
            if factory_installed:
                loop.set_task_factory(prior_factory)
            try:
                if app is not None:
                    await app.state.attachments.close()
            finally:
                try:
                    await (
                        store.close()
                    )  # the guard survives every application connection closing/reconnecting
                finally:
                    try:
                        if guard is not None:
                            cursor = await guard_conn.execute(
                                "SELECT pg_advisory_unlock(hashtext('keviq_test')) AS unlocked, "
                                "pg_backend_pid() AS backend_pid"
                            )
                            released = (await cursor.fetchall())[0]
                            print(
                                "PG_TEST_GUARD "
                                + json.dumps(
                                    {"process_pid": os.getpid(), "acquired_backend_pid": guard, **released},
                                    sort_keys=True,
                                )
                            )
                            assert released["unlocked"] and released["backend_pid"] == guard
                    finally:
                        if guard_conn is not None:
                            await guard_conn.close()


async def ingest(client, event):
    response = await client.post(
        f"/api/sessions/{event['session_id']}/events",
        json=[event],
        headers=getattr(client, "source_headers", {}),
    )
    assert response.status_code == 200, response.text
    return response.json()["accepted"][0]["seq"]


def frame(sid, n, ts):
    return make_event("frame", sid, ts, "sim", event_id=f"{sid}.fr-{n}", jpeg_path=f"synthetic/f_{n:05}.jpg",
                      width=360, height=640)  # fmt: skip


def product(event):
    return {"type": "product_seen", "category": "sneaker", "attributes": {"color": "trắng"},
            "confidence": 1.0, "is_new": True, "frame_ts_ms": event["ts_ms"]}  # fmt: skip


def counts_valid(stats):
    for kind in ("fast", "vision"):
        row = stats[kind]
        assert row["received"] == sum(row[k] for k in (*OUTCOMES, "waiting", "in_flight")), row
    assert stats["vision"]["waiting"] <= 1 and stats["vision"]["in_flight"] <= 1, stats


@asynccontextmanager
async def running(client, handlers, sid, monkeypatch, speed=SPEED, progress=False, attachment_auth=None):
    assert speed >= 1
    monkeypatch.setattr(labels, "BATCH_WAIT_S", 1 / speed)
    monkeypatch.setattr(runner_mod, "VISION_TIMEOUT_S", 10 / speed, raising=False)
    reporter = None
    if progress:
        for name, method in list(vars(Runner).items()):
            if callable(method):
                monkeypatch.setattr(Runner, name, method)
        monkeypatch.setattr(c5_progress, "EVERY_S", 0.25 / speed)
        monkeypatch.setattr(c5_progress, "BEAT_S", 1 / speed)
        reporter = Receipt()
        reporter.counter = c5_progress.install(reporter.say)
    worker = Runner("http://test", handlers, http=client, attachment_auth=attachment_auth)
    task = asyncio.create_task(worker.run(cursor=0, session=sid))
    if reporter:
        await asyncio.sleep(0)  # run() has installed the reporter's own-task set before its first HTTP read
        reporter.counter.own = Infrastructure(reporter.counter.own)
    try:
        yield worker, task, reporter
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        owned = [task for lane in [getattr(worker, "fast", None), *getattr(worker, "vision", {}).values()]
                 if lane is not None for task in lane.tasks]  # fmt: skip
        for job in owned:
            job.cancel()
        await asyncio.gather(*owned, return_exceptions=True)


class Receipt:
    progress = progress_at = None

    def say(self, kind, **fields):
        self.progress, self.progress_at = fields, time.monotonic()


class Shop:
    def see(self, event):
        pass


async def eventually(predicate, seconds=2):
    end = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < end, "test precondition was not reached"
        await asyncio.sleep(0.005)


def save_summary(row):
    print(
        "SLOW_VISION " + json.dumps({k: v for k, v in row.items() if k != "terminal_frames"}, sort_keys=True)
    )
    if dest := os.environ.get("SLOW_VISION_REPORT"):
        with Path(dest).open("a") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def test_runner_exposes_conserved_lane_counts():
    assert callable(getattr(Runner, "stats", None)), "runner.stats() is required to count unfinished input"


async def test_full_loop_digit_phone_hide_is_not_held_behind_vision(pg_app, monkeypatch):
    store, client, sid = pg_app
    moderator = SimModerator()
    labeler = labels.Labeler(client_for=lambda ctx: NoModel(), moderator=moderator)
    started, ended, released, samples, controls = {}, {}, {}, [], {}
    if hasattr(Runner, "release"):
        release = Runner.release

        def capture(self, lane, event, outcome):
            release(self, lane, event, outcome)
            if lane.kind == "vision":
                assert event["event_id"] not in released, "duplicate terminal frame outcome"
                released[event["event_id"]] = outcome

        monkeypatch.setattr(Runner, "release", capture)

    async def fake_see_product(event, ctx):
        eid = event["event_id"]
        started[eid] = time.monotonic()
        n = int(eid.rsplit("-", 1)[1])
        await asyncio.sleep((2 + n % 2) / SPEED)
        ended[eid] = time.monotonic()
        return [product(event)]

    async def control(event, ctx):
        controls[event["event_id"]] = now_ms()
        return []

    handlers = labeler.handlers() | {"frame": [fake_see_product], "speech": [control]}
    monkeypatch.setitem(runner_mod.BACKLOGS, "labels", labeler.backlog) if hasattr(
        runner_mod, "BACKLOGS"
    ) else None
    rows = [json.loads(line) for line in (ROOT / "data/comments/c5/stream-1.jsonl").read_text().splitlines()]
    t0 = now_ms() + 50
    schedule = [(n * 1000, frame(sid, n, t0 + n * 1000)) for n in range(351)]
    for row in rows:
        event = make_event("comment", sid, t0 + row["ts_ms"], "sim", event_id=f"{sid}.{row['comment_id']}",
                           comment_id=row["comment_id"], user_ref=row["user_ref"], text=row["text"], reply_to=None)  # fmt: skip
        schedule.append((row.get("arrives_ms", row["ts_ms"]), event))
    for at in (2500, 3500):
        schedule.append((at, make_event("speech", sid, t0 + at, "sim", text="synthetic control", final=True,
                                        lang="vi", start_ms=t0 + at, end_ms=t0 + at)))  # fmt: skip
    schedule.sort(key=lambda item: item[0])
    began = time.monotonic()
    try:
        async with running(client, handlers, sid, monkeypatch, progress=hasattr(Runner, "stats")) as (
            worker,
            _,
            receipt,
        ):
            for at, event in schedule:
                await asyncio.sleep(max(0, began + at / (1000 * SPEED) - time.monotonic()))
                await ingest(client, event)
                if hasattr(worker, "stats"):
                    stats = worker.stats()
                    counts_valid(stats)
                    samples.append(stats)
            await asyncio.sleep(max(0, began + LOOP_MS / (1000 * SPEED) - time.monotonic()))
            # Allow the final batch/frame to drain; an old serial worker cannot hide an arbitrary backlog here.
            end = time.monotonic() + 6
            while time.monotonic() < end:
                if hasattr(worker, "stats"):
                    stats = worker.stats()
                    counts_valid(stats)
                    samples.append(stats)
                    if all(not row["waiting"] and not row["in_flight"] for row in stats.values()):
                        break
                await asyncio.sleep(0.01)
            barrier = None
            if receipt:
                watch = c5_live.Watch(client, sid, Feed(), Shop())
                polling = asyncio.create_task(watch.run())
                receipt.counter.own.add(polling)
                try:
                    barrier = await watch.barrier(receipt, [set()], timeout_s=10)
                finally:
                    await watch.halt(polling)
            events = await store.read(sid, 0, 5000)
            comments = {e["comment_id"]: e for e in events if e["type"] == "comment"}
            first_hide = {}
            for event in events:
                if event["type"] == "comment_labeled" and event["action"] == "hide":
                    first_hide.setdefault(event["comment_id"], event)
            from worker.brain.shield import PHONE

            phone_ids = {r["comment_id"] for r in rows if PHONE.search(r["text"])}
            delays = {cid: (first_hide[cid]["ingested_ms"] - comments[cid]["ingested_ms"]) * SPEED
                      for cid in phone_ids if cid in first_hide}  # fmt: skip
            summary = {"session": sid, "speed": SPEED, "logical_duration_ms": LOOP_MS,
                       "wall_elapsed_s": round(time.monotonic() - began, 3), "frames": 351,
                       "comments": len(comments), "digit_phone_expected": len(phone_ids),
                       "digit_phone_hidden": len(delays), "hide_logical_ms": delays,
                       "vision_started": len(started), "vision_completed": len(ended),
                       "terminal_frames": released, "stats": worker.stats() if hasattr(worker, "stats") else None,
                       "model_calls": sum(e["type"] == "model_call" for e in events), "barrier": barrier,
                       "control_logical_ms": {e["event_id"]: (controls[e["event_id"]] - e["ingested_ms"]) * SPEED
                                              for e in events if e["event_id"] in controls}}  # fmt: skip
            save_summary(summary)
            assert set(delays) == phone_ids, "a digit-phone comment was never hidden"
            assert max(delays.values()) <= 5000, delays  # deliberately NOT latency_ms
            assert {h["comment_id"] for h in moderator.hidden} >= phone_ids
            assert len(summary["control_logical_ms"]) == 2
            assert max(summary["control_logical_ms"].values()) <= 5000
            assert summary["model_calls"] == 0
            assert set(comments) == {r["comment_id"] for r in rows}
            assert {e["comment_id"] for e in events if e["type"] == "comment_labeled"} == set(comments)
            assert comments["c5-1-095"]["ts_ms"] == t0 + 335825
            assert comments["c5-1-098"]["ts_ms"] == t0 + 339875
            assert summary["wall_elapsed_s"] >= LOOP_MS / (1000 * SPEED)
            assert {e["caused_by"][0] for e in events if e["type"] == "product_seen"} == set(ended)
            assert set(released) == {e["event_id"] for _, e in schedule if e["type"] == "frame"}
            assert all(not row["waiting"] and not row["in_flight"] for row in worker.stats().values())
            assert worker.stats()["vision"]["skipped"] > 0
            assert samples
            assert barrier and barrier["status"] == "SETTLED", barrier
    finally:
        for task in list(labeler.tasks):
            task.cancel()
        await asyncio.gather(*list(labeler.tasks), return_exceptions=True)


@pytest.mark.parametrize("stop_worker", [False, True])
async def test_real_barrier_waits_for_pending_vision_and_stopped_worker_is_incomplete(
    pg_app, monkeypatch, stop_worker
):
    store, client, sid = pg_app
    gate, entered = asyncio.Event(), asyncio.Event()

    async def pending(event, ctx):
        entered.set()
        await gate.wait()
        return [product(event)]

    async with running(client, {"frame": [pending]}, sid, monkeypatch, progress=True) as (
        worker,
        task,
        receipt,
    ):
        head = await ingest(client, frame(sid, 0, now_ms()))
        await asyncio.wait_for(entered.wait(), 2)
        await eventually(lambda: receipt.progress and receipt.progress["pending"] == 1)
        watch = c5_live.Watch(client, sid, Feed(), Shop())
        watch.seqs = [head]
        early = await watch.barrier(receipt, [set()], timeout_s=0)
        assert early["status"] == "INCOMPLETE" and early["pending"] == 1
        counts_valid(worker.stats())
        if stop_worker:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            stopped = await watch.barrier(receipt, [set()], timeout_s=0.1)
            assert stopped["status"] == "INCOMPLETE" and stopped["pending"] == 1
        else:
            gate.set()
            polling = asyncio.create_task(watch.run())
            receipt.counter.own.add(polling)  # measurement-side polling is not worker background work
            try:
                drained = await watch.barrier(receipt, [set()], timeout_s=5)
                assert drained["status"] == "SETTLED" and drained["pending"] == 0, drained
            finally:
                await watch.halt(polling)
        save_summary(
            {
                "case": "stopped" if stop_worker else "drain",
                "early": early,
                "final": stopped if stop_worker else drained,
            }
        )


async def test_failed_and_stale_frames_are_counted_once(pg_app, monkeypatch):
    _, client, sid = pg_app
    entered = asyncio.Event()

    async def fail(event, ctx):
        entered.set()
        await asyncio.sleep(2 / SPEED)
        raise RuntimeError("synthetic vision failure")

    async with running(client, {"frame": [fail]}, sid, monkeypatch) as (worker, _, _):
        ts = now_ms()
        await ingest(client, frame(sid, 1, ts + 1000))
        await asyncio.wait_for(entered.wait(), 2)
        await ingest(client, frame(sid, 0, ts))
        await eventually(lambda: worker.stats()["vision"]["failed"] == 1)
        stats = worker.stats()
        counts_valid(stats)
        assert stats["vision"]["received"] == 2
        assert stats["vision"]["failed"] == stats["vision"]["stale"] == 1
