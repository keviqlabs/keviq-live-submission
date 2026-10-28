"""The worker's own count of what it has finished, for the C5 loop-end barrier (measure/c5_live Watch.barrier):
a loop must not end on silence while the worker still has a backlog (RC2).

measure/c5_proc installs it in the worker process before the worker starts, around worker.runner.Runner (no
worker code changes): an event the runner hands to handle() is pending until handle() has finished with it, and
work a handler left running in the background (labels batches and their timers, speech decisions: any task the
worker's loop started after the runner did, but this reporter) is counted too. A "progress" C5PROC line, on a
change at most every 0.25 s and at least once a second, says:
  start_seq   the cursor the runner started from (the head, as after a restart)
  delivered   events handed to handle(); finished: of them, handle() finished; errors: of those, it raised
  pending     delivered and not finished; running: inside handle() now
  background  the worker's other tasks still running
  done_seq    every delivered event at or below it is finished (the continuous watermark); start_seq before any
  n           the line's number, so the runner can tell a newer line from an old one
Ids and numbers only."""

import asyncio

EVERY_S, BEAT_S = 0.25, 1.0


class Progress:
    def __init__(self):
        self.start_seq = self.last_seq = None
        self.pending: dict[str, int | None] = {}  # event_id -> seq: delivered, not finished
        self.delivered = self.finished = self.errors = self.running = self.n = 0
        self.loop = None  # the worker's loop
        self.own: set = set()  # its tasks that are not work: those from before the runner, the reporter

    def deliver(self, seq: int | None, event: dict) -> None:
        self.delivered += 1
        self.last_seq = seq if seq is not None else self.last_seq
        self.pending[event["event_id"]] = self.last_seq

    def finish(self, event: dict) -> None:
        if self.pending.pop(event["event_id"], False) is not False:
            self.finished += 1

    def done_seq(self) -> int | None:
        seqs = [s for s in self.pending.values() if s is not None]
        if seqs:
            return min(seqs) - 1
        return self.last_seq if self.delivered else self.start_seq

    def background(self) -> int:
        if self.loop is None:
            return 0
        return sum(not t.done() for t in asyncio.all_tasks(self.loop) if t not in self.own)

    def line(self) -> dict:
        return {"start_seq": self.start_seq, "delivered": self.delivered, "finished": self.finished,
                "errors": self.errors, "pending": len(self.pending), "running": self.running,
                "background": self.background(), "done_seq": self.done_seq()}  # fmt: skip


async def report(p: Progress, say) -> None:
    loop = asyncio.get_running_loop()
    last, at = None, 0.0
    while True:
        line = p.line()
        if line != last or loop.time() - at >= BEAT_S:
            p.n += 1
            say("progress", n=p.n, **line)
            last, at = line, loop.time()
        await asyncio.sleep(EVERY_S)


def install(say) -> Progress:
    """Wraps worker.runner.Runner.handle and Runner.run; returns the live count."""
    from worker import runner

    p = Progress()
    handle, run = runner.Runner.handle, runner.Runner.run

    async def handling(self, event: dict) -> list[dict]:
        p.deliver(self.cursor, event)  # Runner.run sets the cursor to the event's seq before handle()
        p.running += 1
        try:
            return await handle(self, event)
        except Exception:
            p.errors += 1
            raise
        finally:
            p.running -= 1
            p.finish(event)

    async def running(self, cursor: int | None = None, session: str | None = None) -> None:
        if cursor is None:
            cursor = (await self.http.get("/healthz")).json()["head_seq"]
        p.start_seq, p.loop = cursor, asyncio.get_running_loop()
        beat = asyncio.create_task(report(p, say))
        p.own = asyncio.all_tasks() | {beat}  # the runner's own task and whatever ran before it
        try:
            await run(self, cursor, session)
        finally:
            beat.cancel()

    runner.Runner.handle, runner.Runner.run = handling, running
    return p
