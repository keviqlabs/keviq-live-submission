"""The worker's own count of what it has finished, for the C5 loop-end barrier (measure/c5_live Watch.barrier):
a loop must not end on silence while the worker still has a backlog (RC2).

measure/c5_proc installs it in the worker process before the worker starts, around worker.runner.Runner (no
worker code changes): an event the runner puts in a lane (or one handed to handle() directly) is pending until it
has left its lane (or handle() has finished with it), and work handlers keep in the background is counted twice
over: as tasks (the lanes', the labels consumers', speech decisions and cut-outs: any task the worker's loop
started after the runner did, but this reporter) and as what worker.runner.BACKLOGS say is waiting or in flight.
A "progress" C5PROC line, on a change at most every 0.25 s and at least once a second, says:
  start_seq   the cursor the runner started from (the head, as after a restart)
  delivered   events put in a lane or handed to handle(); finished: of them, those that left (or handle() finished)
  errors      of those, failed in their lane (or handle() raised); skipped, stale: frames the vision lane let go
  pending     delivered and not finished; running: being handled now
  background  the worker's other tasks still running
  queued      waiting or in flight in BACKLOGS (labels, speech, cut-outs)
  done_seq    every delivered event at or below it is finished (the continuous watermark); start_seq before any
  n           the line's number, so the runner can tell a newer line from an old one
Ids and numbers only."""

import asyncio

EVERY_S, BEAT_S = 0.25, 1.0


class Progress:
    def __init__(self):
        self.start_seq = self.last_seq = None
        self.pending: dict[str, int | None] = {}  # event_id -> seq: delivered, not finished
        self.delivered = self.finished = self.errors = self.skipped = self.stale = self.running = self.n = 0
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

    def queued(self) -> int:
        from worker.runner import BACKLOGS

        return sum(b["waiting"] + b["in_flight"] for b in (backlog() for backlog in BACKLOGS.values()))

    def line(self) -> dict:
        return {"start_seq": self.start_seq, "delivered": self.delivered, "finished": self.finished,
                "errors": self.errors, "skipped": self.skipped, "stale": self.stale, "pending": len(self.pending),
                "running": self.running, "background": self.background(), "queued": self.queued(),
                "done_seq": self.done_seq()}  # fmt: skip


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
    """Wraps worker.runner.Runner's handle, receive, release, process and run; returns the live count."""
    from worker import runner

    p = Progress()
    handle, run = runner.Runner.handle, runner.Runner.run
    receive, release, process = runner.Runner.receive, runner.Runner.release, runner.Runner.process

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

    def receiving(self, event: dict) -> None:
        p.deliver(self.cursor, event)  # Runner.run sets the cursor to the event's seq before receive()
        receive(self, event)

    def releasing(self, lane, event: dict, outcome: str) -> None:
        release(self, lane, event, outcome)
        p.errors += outcome == "failed"
        p.skipped += outcome == "skipped"
        p.stale += outcome == "stale"
        p.finish(event)

    async def processing(self, event: dict, received_ms: int, limit_s: float | None = None) -> str:
        p.running += 1
        try:
            return await process(self, event, received_ms, limit_s)
        finally:
            p.running -= 1

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
    runner.Runner.receive, runner.Runner.release, runner.Runner.process = receiving, releasing, processing
    return p
