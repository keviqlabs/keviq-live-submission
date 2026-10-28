"""measure/scenarios Run.settle: a read waits until the worker has finished the session's work, by the worker's own
count (its C5PROC progress lines, measure/c5_progress), never on quiet alone; the barrier of measure/c5_live (#41).
The worker here is the real worker.runner.Runner on the whole stream, as tests/integration.sh runs it."""

import asyncio
import contextlib
import json
import threading
import time

from measure import c5_proc, c5_progress, scenarios
from worker import runner


class Reply:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        pass


class Follow:
    """The worker's follow stream of /api/events from its cursor: rows as they come, then it holds."""

    def __init__(self, app: "App", cursor: int):
        self.app, self.cursor = app, cursor

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        while True:
            for seq, ev in self.app.rows[self.cursor :]:
                self.cursor = seq
                for line in (f"id: {seq}", f"data: {json.dumps(ev)}", ""):
                    yield line
            await asyncio.sleep(0.01)


class App:
    """The store as the worker and the scenarios see it, from both threads: each event added takes the next seq."""

    def __init__(self):
        self.rows: list[tuple[int, dict]] = []
        self.lock = threading.Lock()

    def add(self, *events: dict) -> None:
        with self.lock:
            for ev in events:
                self.rows.append((len(self.rows) + 1, ev))

    async def get(self, url, params=None):  # the scenarios' read of one session, follow=false
        sid = url.split("/")[3]
        rows = [(s, e) for s, e in self.rows if e["session_id"] == sid]
        return Reply("".join(f"id: {s}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n" for s, e in rows))

    async def post(self, url, json=None):
        self.add(*json)
        return Reply("")

    @contextlib.asynccontextmanager
    async def stream(self, method, url, params=None):
        yield Follow(self, params["cursor"])


class WorkerThread:
    """The real worker.runner.Runner on the whole stream, in a thread with its own loop: the separate process
    tests/integration.sh starts. measure/c5_progress counts the tasks of the worker's loop as its background work,
    so the scenarios' own tasks must not be in it."""

    def __init__(self, app: App, handlers: dict):
        self.runner = runner.Runner("http://app", handlers, http=app)
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.main)

    async def work(self) -> None:
        self.loop, self.task = asyncio.get_running_loop(), asyncio.current_task()
        self.ready.set()
        await self.runner.run(cursor=0)

    def main(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            asyncio.run(self.work())

    def __enter__(self):
        self.thread.start()
        self.ready.wait(5)
        return self

    def __exit__(self, *exc):
        self.loop.call_soon_threadsafe(self.task.cancel)
        self.thread.join(5)


def event(event_id: str, etype: str, session: str = "s") -> dict:
    return {"event_id": event_id, "type": etype, "session_id": session}


def worker_log(path):
    """c5_proc.say, into the file tests/integration.sh sends the worker's stdout to."""

    def say(kind: str, **fields) -> None:
        with path.open("a") as f:
            f.write(c5_proc.TAG + json.dumps({"kind": kind, **fields}) + "\n")

    path.write_text("worker: consuming /api/events from seq 0\n")
    return say


def restore_runner(monkeypatch) -> None:
    """c5_progress.install patches worker.runner in place, as in the worker process; undone after the test."""
    monkeypatch.setattr(runner.Runner, "handle", runner.Runner.handle)
    monkeypatch.setattr(runner.Runner, "run", runner.Runner.run)


def with_worker(app: App, log, handlers: dict, body):
    with WorkerThread(app, handlers):
        return asyncio.run(body(scenarios.Run(app, "http://app", 20.0, worker_log=log)))


def test_settle_waits_for_a_handler_that_is_three_seconds_into_a_comment(monkeypatch, tmp_path):
    """The probe: quiet for 2 s said done while handle() still had 1 s to go and its consequence was not posted."""
    restore_runner(monkeypatch)
    log = tmp_path / "worker.log"
    c5_progress.install(worker_log(log))
    app = App()

    async def slow(ev, ctx):
        await asyncio.sleep(3.0)
        app.add({**event("out-1", "comment_labeled"), "caused_by": [ev["event_id"]]})
        return []

    async def body(run):
        app.add(event("cm-1", "comment"))
        t = time.monotonic()
        evs = await run.settle("s", limit_s=10.0)
        return evs, time.monotonic() - t, run

    evs, waited_s, run = with_worker(app, log, {"comment": [slow]}, body)
    assert [e["event_id"] for e in evs] == ["cm-1", "out-1"]
    assert waited_s >= 3.0
    assert [r["status"] for _, r in run.barriers] == ["SETTLED"]
    assert {k: run.barriers[0][1][k] for k in ("head_seq", "done_seq", "pending", "background")} == {
        "head_seq": 2, "done_seq": 2, "pending": 0, "background": 0}  # fmt: skip


def test_the_worker_past_the_session_head_on_other_sessions_is_done_with_this_one(monkeypatch, tmp_path):
    """The integration worker follows the whole stream: its watermark past this session's head is done, not behind."""
    restore_runner(monkeypatch)
    log = tmp_path / "worker.log"
    c5_progress.install(worker_log(log))
    app = App()

    async def body(run):
        app.add(event("cm-1", "comment"), event("cm-2", "comment", session="other"))
        evs = await run.settle("s", limit_s=10.0)
        return evs, run

    evs, run = with_worker(app, log, {}, body)
    assert [e["event_id"] for e in evs] == ["cm-1"]
    status, row = run.barriers[0][1]["status"], run.barriers[0][1]
    assert (status, row["head_seq"], row["done_seq"]) == ("SETTLED", 1, 2)


def test_a_read_that_times_out_is_a_failed_check_with_what_was_left(monkeypatch, tmp_path):
    restore_runner(monkeypatch)
    log = tmp_path / "worker.log"
    c5_progress.install(worker_log(log))
    app = App()

    async def stuck(ev, ctx):
        await asyncio.sleep(30.0)
        return []

    async def body(run):
        app.add(event("cm-1", "comment"))
        await run.settle("s", limit_s=1.5)
        run.barrier_check()
        return run

    run = with_worker(app, log, {"comment": [stuck]}, body)
    kind, name, ok, detail = run.rows[-1]
    assert (kind, ok) == ("CHECK", False)
    assert "s INCOMPLETE" in detail and "pending 1" in detail and "running 1" in detail


def test_the_barrier_check_passes_when_every_read_settled():
    run = scenarios.Run(None, "http://app", 20.0, worker_log=None)
    run.barriers = [
        ("a", {"status": "SETTLED", "waited_ms": 1200}),
        ("b", {"status": "SETTLED", "waited_ms": 900}),
    ]
    run.barrier_check()
    kind, name, ok, detail = run.rows[-1]
    assert (kind, ok) == ("CHECK", True) and "2 reads" in name
    run.barriers.append(("c", {"status": "INCOMPLETE", "waited_ms": 60000, "pending": 0, "background": 2}))
    run.barrier_check()
    assert run.rows[-1][2] is False


def test_the_worker_log_keeps_whole_progress_lines_only(tmp_path):
    log = tmp_path / "worker.log"
    tag = c5_proc.TAG
    log.write_text("worker: consuming /api/events from seq 0\n" + tag + '{"kind": "proof", "sha": "x"}\n'
                   + tag + '{"kind": "progress", "n": 1, "pend')  # fmt: skip
    w = scenarios.WorkerLog(log)
    w.read()
    assert w.progress is None
    with log.open("a") as f:
        f.write('ing": 0}\n' + tag + "{not json\n" + "2026 worker INFO a log line\n")
    w.read()
    assert w.progress == {"kind": "progress", "n": 1, "pending": 0} and w.progress_at is not None
