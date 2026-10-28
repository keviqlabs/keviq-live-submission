"""C5PROC exposes the runner's lane counters alongside the existing progress totals."""

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager

from measure import c5_proc, c5_progress
from worker import runner
from worker.brain import card


def test_progress_before_run_keeps_the_existing_totals_and_has_no_runner_stats(monkeypatch):
    monkeypatch.setattr(runner, "BACKLOGS", {})
    p = c5_progress.Progress()
    assert p.line() == {
        "start_seq": None, "delivered": 0, "finished": 0, "errors": 0, "skipped": 0, "stale": 0,
        "pending": 0, "running": 0, "background": 0, "queued": 0, "done_seq": None, "stats": {},
    }  # fmt: skip


class Stream:
    def __init__(self, events):
        self.events = events
        self.delivered = asyncio.Event()

    @asynccontextmanager
    async def stream(self, method, url, params=None):
        yield self

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for seq, event in enumerate(self.events, 1):
            for line in (f"id: {seq}", f"data: {json.dumps(event)}", ""):
                yield line
        self.delivered.set()
        await asyncio.Event().wait()


async def test_periodic_and_final_progress_keep_live_lane_counts_and_compatible_totals(monkeypatch, capsys):
    for name, method in list(vars(runner.Runner).items()):
        if callable(method):
            monkeypatch.setattr(runner.Runner, name, method)
    monkeypatch.setattr(c5_progress, "EVERY_S", 0.001)
    backlog = {"waiting": 2, "in_flight": 1}
    monkeypatch.setattr(runner, "BACKLOGS", {"labels": lambda: dict(backlog)})
    rows = []
    changed = asyncio.Event()

    def say(kind, **fields):
        c5_proc.say(kind, **fields)
        rows.append({"kind": kind, **fields})
        changed.set()

    async def wait_for_stats(stats):
        async with asyncio.timeout(2):
            while not any(row.get("stats") == stats for row in rows):
                changed.clear()
                await changed.wait()

    p = c5_progress.install(say)
    release = asyncio.Event()
    vision_started, fast_started = asyncio.Event(), asyncio.Event()

    async def vision(event, ctx):
        vision_started.set()
        await release.wait()
        return []

    async def fast(event, ctx):
        if event["event_id"] == "cm-2":
            raise ValueError("synthetic failure")
        if event["event_id"] == "cm-3":
            fast_started.set()
            await release.wait()
        return []

    frames = [{"event_id": f"fr-{n}", "session_id": "s", "type": "frame", "ts_ms": ts}
              for n, ts in enumerate((1000, 2000, 3000, 2000), 1)]  # fmt: skip
    comments = [{"event_id": f"cm-{n}", "session_id": "s", "type": "comment"} for n in range(1, 5)]
    http = Stream([*frames, *comments])
    w = runner.Runner("http://test", {"frame": [vision], "comment": [fast]}, http=http)
    task = asyncio.create_task(w.run(cursor=0))
    try:
        await asyncio.wait_for(
            asyncio.gather(http.delivered.wait(), vision_started.wait(), fast_started.wait()), timeout=2
        )
        mid_stats = {
            "fast": {"received": 4, "done": 1, "skipped": 0, "stale": 0, "failed": 1,
                     "in_flight": 1, "waiting": 1},
            "vision": {"received": 4, "done": 0, "skipped": 1, "stale": 1, "failed": 0,
                       "in_flight": 1, "waiting": 1},
            "labels": {"waiting": 2, "in_flight": 1},
        }  # fmt: skip
        mid = p.line()
        assert mid["stats"] == w.stats() == mid_stats
        assert {k: mid[k] for k in (
            "start_seq", "delivered", "finished", "errors", "skipped", "stale", "pending", "running",
            "queued", "done_seq",
        )} == {"start_seq": 0, "delivered": 8, "finished": 4, "errors": 1, "skipped": 1, "stale": 1,
               "pending": 4, "running": 2, "queued": 3, "done_seq": 0}  # fmt: skip
        await wait_for_stats(mid_stats)
        final_stats = {
            "fast": {"received": 4, "done": 3, "skipped": 0, "stale": 0, "failed": 1,
                     "in_flight": 0, "waiting": 0},
            "vision": {"received": 4, "done": 2, "skipped": 1, "stale": 1, "failed": 0,
                       "in_flight": 0, "waiting": 0},
            "labels": {"waiting": 0, "in_flight": 0},
        }  # fmt: skip
        release.set()
        backlog.update(waiting=0, in_flight=0)
        await wait_for_stats(final_stats)
        assert p.line()["stats"] == w.stats() == final_stats
        assert mid["stats"] == mid_stats  # a prior line remains a snapshot

        monkeypatch.setattr(c5_proc, "PROGRESS", p)
        monkeypatch.setattr(card, "SESSIONS", {})
        exits = []
        monkeypatch.setattr(c5_proc.os, "_exit", exits.append)
        n = p.n
        c5_proc.on_term(None, None)
        assert exits == [0]
        lines = [json.loads(line[len(c5_proc.TAG) :]) for line in capsys.readouterr().out.splitlines()]
        assert all(line["pid"] > 0 for line in lines)
        assert any(line.get("stats") == mid_stats for line in lines)
        final = lines[-2]
        assert final["kind"] == "progress" and final["n"] == n + 1
        assert final["stats"] == final_stats
        assert {k: final[k] for k in (
            "delivered", "finished", "errors", "skipped", "stale", "pending", "running", "queued", "done_seq",
        )} == {"delivered": 8, "finished": 8, "errors": 1, "skipped": 1, "stale": 1,
               "pending": 0, "running": 0, "queued": 0, "done_seq": 8}  # fmt: skip
        for line in (mid, final):
            for kind in ("fast", "vision"):
                lane = line["stats"][kind]
                assert lane["received"] == sum(
                    lane[k]
                    for k in (
                        "done",
                        "skipped",
                        "stale",
                        "failed",
                        "in_flight",
                        "waiting",
                    )
                )
        assert lines[-1]["kind"] == "waits"
    finally:
        release.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
