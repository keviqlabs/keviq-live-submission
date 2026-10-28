"""The C5 loop ends at a barrier, never on silence (RC2): the worker's own count of what it has finished
(measure/c5_progress, around the real worker.runner.Runner) against the session head (measure/c5_live Watch.barrier).
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager

from measure import c5_live, c5_proc, c5_progress
from measure.c5_shop import Feed
from worker import runner


def sse(seq: int, event: dict) -> list[str]:
    return [f"id: {seq}", f"data: {json.dumps(event)}", ""]


def comment(n: int) -> dict:
    return {"event_id": f"ev-{n}", "type": "comment", "session_id": "run"}


class WorkerHttp:
    """The worker's stream: these lines once, then it holds like a follow stream with nothing new."""

    def __init__(self, lines: list[str]):
        self.lines = lines

    @asynccontextmanager
    async def stream(self, method, url, params=None):
        yield self

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        lines, self.lines = self.lines, []
        for line in lines:
            yield line
        await asyncio.Event().wait()


class PollHttp:
    """The runner's poll of the session: nothing new each time."""

    @asynccontextmanager
    async def stream(self, method, url, params=None):
        yield self

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in ():
            yield line


class Shop:
    def see(self, ev):
        pass


class Worker:
    """What measure/c5_live.Child keeps of the worker's progress lines."""

    progress = progress_at = None

    def say(self, kind: str, **fields) -> None:
        self.progress, self.progress_at = {"kind": kind, **fields}, time.monotonic()


def restore_runner(monkeypatch) -> None:
    """c5_progress.install patches worker.runner.Runner in place, as in the worker process; every method of it is
    put back after the test."""
    for name, method in list(vars(runner.Runner).items()):
        if callable(method):
            monkeypatch.setattr(runner.Runner, name, method)


def test_a_worker_two_seconds_into_an_event_is_not_settled_after_half_a_second(monkeypatch):
    """The RC2 probe: the old quiet() said quiet after 501 ms while handle() still had 2 s to go."""
    restore_runner(monkeypatch)
    worker = Worker()
    c5_progress.install(worker.say)

    async def slow(event, ctx):
        await asyncio.sleep(2.0)
        return []

    async def scenario():
        w = runner.Runner("http://app", {"comment": [slow]}, http=WorkerHttp(sse(7, comment(7))))
        working = asyncio.create_task(w.run(cursor=5, session="run"))
        watch = c5_live.Watch(PollHttp(), "run", Feed(), Shop())
        watch.seqs = [7]  # the session head: the one event
        watching = asyncio.create_task(watch.run())
        t = time.monotonic()
        early = await watch.barrier(worker, [set()], timeout_s=0.5)
        early_ms = (time.monotonic() - t) * 1000
        late = await watch.barrier(worker, [set()], timeout_s=5.0)
        late_ms = (time.monotonic() - t) * 1000
        working.cancel()
        await watch.halt(watching)
        return early, early_ms, late, late_ms

    early, early_ms, late, late_ms = asyncio.run(scenario())
    assert early["status"] == "INCOMPLETE" and early_ms < 2000
    assert {k: early[k] for k in ("head_seq", "done_seq", "behind", "delivered", "pending", "running")} == {
        "head_seq": 7, "done_seq": 6, "behind": 1, "delivered": 1, "pending": 1, "running": 1}  # fmt: skip
    assert late["status"] == "SETTLED" and late_ms >= 2000
    assert {k: late[k] for k in ("done_seq", "behind", "finished", "errors", "pending", "running", "background")} == {
        "done_seq": 7, "behind": 0, "finished": 1, "errors": 0, "pending": 0, "running": 0, "background": 0}  # fmt: skip


def test_work_a_handler_left_in_the_background_holds_the_barrier(monkeypatch):
    """A labels batch or a speech decision runs after handle() returned: done_seq is at the head, nothing is
    pending, and the loop is still not settled until that task ends."""
    restore_runner(monkeypatch)
    worker = Worker()
    c5_progress.install(worker.say)
    later = set()

    async def queue(event, ctx):
        later.add(asyncio.create_task(asyncio.sleep(3.0)))
        return []

    async def scenario():
        w = runner.Runner("http://app", {"comment": [queue]}, http=WorkerHttp(sse(7, comment(7))))
        working = asyncio.create_task(w.run(cursor=5, session="run"))
        watch = c5_live.Watch(PollHttp(), "run", Feed(), Shop())
        watch.seqs = [7]
        watching = asyncio.create_task(watch.run())
        t = time.monotonic()
        early = await watch.barrier(worker, [set()], timeout_s=2.0)  # long enough to settle without the task
        late = await watch.barrier(worker, [set()], timeout_s=8.0)
        late_ms = (time.monotonic() - t) * 1000
        working.cancel()
        await watch.halt(watching)
        return early, late, late_ms

    early, late, late_ms = asyncio.run(scenario())
    assert early["status"] == "INCOMPLETE"
    assert {k: early[k] for k in ("done_seq", "behind", "pending", "running", "background")} == {
        "done_seq": 7, "behind": 0, "pending": 0, "running": 0, "background": 1}  # fmt: skip
    assert late["status"] == "SETTLED" and late["background"] == 0 and late_ms >= 3000


def test_work_queued_with_no_task_yet_holds_the_barrier(monkeypatch):
    """Comments waiting for their labels batch, lines waiting for a read slot, live cards waiting for SAM: no task
    may hold them yet, and the loop is not settled while worker.runner.BACKLOGS has any queued."""
    restore_runner(monkeypatch)
    worker = Worker()
    c5_progress.install(worker.say)
    held = []
    backlog = {"waiting": 0, "in_flight": 0}
    monkeypatch.setitem(getattr(runner, "BACKLOGS", {}), "held", lambda: backlog | {"waiting": len(held)})

    async def hold(event, ctx):
        held.append(event["event_id"])
        return []

    async def scenario():
        w = runner.Runner("http://app", {"comment": [hold]}, http=WorkerHttp(sse(7, comment(7))))
        working = asyncio.create_task(w.run(cursor=5, session="run"))
        watch = c5_live.Watch(PollHttp(), "run", Feed(), Shop())
        watch.seqs = [7]
        watching = asyncio.create_task(watch.run())
        early = await watch.barrier(worker, [set()], timeout_s=2.0)  # long enough to settle without the queue
        held.clear()  # the batch was taken and done
        late = await watch.barrier(worker, [set()], timeout_s=5.0)
        working.cancel()
        await watch.halt(watching)
        return early, late

    early, late = asyncio.run(scenario())
    assert early["status"] == "INCOMPLETE"
    assert {k: early.get(k) for k in ("done_seq", "behind", "pending", "running", "background", "queued")} == {
        "done_seq": 7, "behind": 0, "pending": 0, "running": 0, "background": 0, "queued": 1}  # fmt: skip
    assert late["status"] == "SETTLED" and late["queued"] == 0


def test_install_leaves_parse_sse_alone_so_history_reads_are_not_deliveries(monkeypatch):
    """labels, orders and refused read the session history with worker.runner.parse_sse; those events are
    never handed to handle() and must not count as pending."""
    restore_runner(monkeypatch)
    parse = runner.parse_sse
    c5_progress.install(Worker().say)
    assert runner.parse_sse is parse


def test_the_watermark_is_continuous_when_events_finish_out_of_order():
    p = c5_progress.Progress()
    p.start_seq = 5
    assert p.done_seq() == 5  # nothing delivered: where the runner started
    for n in (7, 8, 9):
        p.deliver(n, comment(n))
    p.finish(comment(8))
    assert (p.done_seq(), p.line()["pending"], p.finished) == (6, 2, 1)
    p.finish(comment(7))
    assert p.done_seq() == 8
    p.deliver(None, comment(10))  # no id line: counted at the last seq seen
    p.finish(comment(9))
    assert p.done_seq() == 8
    p.finish(comment(10))
    p.finish(comment(10))  # finished once
    assert (p.done_seq(), p.line()["pending"], p.delivered, p.finished) == (9, 0, 4, 4)


def check(progress: dict | None, seqs: list[int], age_s: float = 0.0, tasks: list[set] | None = None) -> dict:
    worker = Worker()
    if progress is not None:
        worker.progress, worker.progress_at = progress, time.monotonic() - age_s
    watch = c5_live.Watch(PollHttp(), "run", Feed(), Shop())
    watch.seqs = seqs
    return watch.check(worker, tasks or [set()])


def line(**over) -> dict:
    p = c5_progress.Progress()
    p.start_seq = 5
    return {"kind": "progress", "n": 3, **p.line(), **over}


def test_check_is_ok_only_with_a_fresh_line_nothing_pending_at_the_head_and_no_task_running():
    assert check(line(delivered=2, finished=2, done_seq=9), [8, 9])["ok"]
    assert check(line(), [4, 5])["ok"]  # started at the head, nothing since
    assert not check(line(), [4, 5, 6])["ok"]  # started at the head, one event since, not delivered
    assert not check(None, [9])["ok"]  # no progress line yet
    silent = check(line(delivered=2, finished=2, done_seq=9), [8, 9], age_s=3.0)  # the worker went silent
    assert not silent["ok"]
    behind = check(line(delivered=1, finished=1, done_seq=7), [7, 8, 9])
    assert (behind["ok"], behind["behind"]) == (False, 2)
    assert not check(line(delivered=1, pending=1, done_seq=6), [7])["ok"]
    assert not check(line(delivered=2, finished=2, done_seq=9, background=1), [8, 9])["ok"]

    async def busy():
        task = asyncio.create_task(asyncio.sleep(1))
        try:
            return check(line(delivered=2, finished=2, done_seq=9), [8, 9], tasks=[{task}])
        finally:
            task.cancel()

    row = asyncio.run(busy())
    assert (row["ok"], row["tasks_running"]) == (False, 1)


def test_a_barrier_that_times_out_says_what_was_left():
    async def scenario():
        worker = Worker()
        worker.progress, worker.progress_at = line(delivered=1, finished=1, done_seq=7), time.monotonic()
        watch = c5_live.Watch(PollHttp(), "run", Feed(), Shop())
        watch.seqs = [7, 8, 9]
        return await watch.barrier(worker, [set()], timeout_s=0.6)

    row = asyncio.run(scenario())
    assert (row["status"], row["head_seq"], row["done_seq"], row["behind"]) == ("INCOMPLETE", 9, 7, 2)


class Stdout:
    def __init__(self, lines: list[str]):
        self.lines = [(x + "\n").encode() for x in lines]

    def __aiter__(self):
        return self._lines()

    async def _lines(self):
        for raw in self.lines:
            yield raw


def test_the_runner_keeps_the_last_progress_line_apart_from_the_proof_lines(tmp_path):
    def said(kind, **fields):
        return c5_proc.TAG + json.dumps({"kind": kind, **fields})

    child = c5_live.Child("worker", [], {}, tmp_path / "worker.log")
    child.proc = type("Proc", (), {"stdout": Stdout([said("proof", sha="x"), said("progress", n=1, pending=1),
                                                     said("progress", n=2, pending=0)])})()  # fmt: skip
    asyncio.run(child.read())
    assert set(child.said) == {"proof"}
    assert (child.progress["n"], child.progress["pending"]) == (2, 0) and child.progress_at is not None
