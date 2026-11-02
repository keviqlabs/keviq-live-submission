"""The default number of concurrent reads of a bound session keeps up with a recorded paid C5 run.

tests/fixtures/c5_vision_reads.csv holds, for each of the 1755 frames of one paid C5 run (five loops of the
recording, one frame a second), only when the frame arrived (ms after the first frame), how long its vision read
took (ms) and whether that read failed (129 did). Nothing else from the run is kept.

The worker's own Runner and bound vision lane replay that load unchanged: each frame is put in the lane at its
arrival time and its read takes its recorded time, then returns a recognition (or fails). They run on an event
loop with a virtual clock: when the loop would wait for its next timer, the clock jumps to it instead. The
33-minute run replays in under a second of wall time and comes out the same every time. HTTP is the in-memory
stream of test_worker_bound_lane; prices, cards, orders and model load feedback are not part of the replay.

A frame backs up when it waits longer than VISION_TIMEOUT_S for its read to start: that is the most time the
worker allows one read, so a frame that waits longer before it is even read is behind by more than a whole read.
Replayed here, four readers make frames wait up to 96.6 s (95th percentile 42.3 s) and eight up to 7.9 s (95th
percentile 2.0 s). The reader count tested as the default is the one worker/runner.py sets when
VISION_READS_PER_SESSION is not in the environment.
"""

import asyncio
import csv
import importlib.util
import math
import selectors
import sys
from pathlib import Path

from tests.test_worker_bound_lane import AUTH, BoundHttp, bound, seen
from worker import runner as lanes
from worker.runner import VISION_TIMEOUT_S, Failed, Runner

READS = [
    (int(row["arrival_ms"]) / 1000, int(row["read_ms"]) / 1000, row["failed"] == "1")
    for row in csv.DictReader((Path(__file__).parent / "fixtures" / "c5_vision_reads.csv").open())
]


class VirtualClock(selectors.DefaultSelector):
    """Never blocks: a wait for the next timer moves the clock to it, then polls I/O without waiting."""

    now = 0.0

    def select(self, timeout=None):
        if timeout is None:
            raise RuntimeError("nothing scheduled: the replay would wait forever")
        self.now += timeout
        return super().select(0)


class VirtualLoop(asyncio.SelectorEventLoop):
    def __init__(self):
        self.clock = VirtualClock()
        super().__init__(self.clock)

    def time(self):
        return self.clock.now


def default_readers(monkeypatch) -> int:
    """VISION_READERS as worker/runner.py sets it with VISION_READS_PER_SESSION unset (a fresh load of the file,
    so an environment or .env value does not change what is tested)."""
    monkeypatch.delenv("VISION_READS_PER_SESSION", raising=False)
    spec = importlib.util.spec_from_file_location("runner_default", lanes.__file__)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)  # its dataclass looks the module up while it loads
    spec.loader.exec_module(module)
    return module.VISION_READERS


async def replay() -> dict:
    """Every recorded frame into one bound session's lane at its arrival time; once all are done, when each was
    put in the lane and when its read started, the posts in order and the lane's counts."""
    loop = asyncio.get_running_loop()
    put, started, durations = {}, {}, {}

    async def read(event, ctx):
        started[event["event_id"]] = loop.time()
        seconds, failed = durations[event["event_id"]]
        await asyncio.sleep(seconds)
        if failed:
            raise Failed
        return seen(event["ts_ms"])

    http = BoundHttp([])
    runner = Runner("http://app", {"frame": [read]}, http=http, attachment_auth=AUTH)
    t0 = loop.time()
    for n, (arrival, seconds, failed) in enumerate(READS, 1):
        await asyncio.sleep(max(0.0, t0 + arrival - loop.time()))
        event = bound(n, round(arrival * 1000) + 1)
        durations[event["event_id"]] = (seconds, failed)
        put[event["event_id"]] = loop.time()
        runner.receive(event)
    lane = runner.vision["b"]
    while lane.tasks:  # a read that finishes starts the next waiting one before it is done
        await asyncio.wait(list(lane.tasks))
    await runner.evidence.close()
    waits = sorted(started[e] - put[e] for e in put)
    return {"waits": waits, "max_wait": waits[-1], "p95_wait": waits[math.ceil(0.95 * len(waits)) - 1],
            "posted": [e["caused_by"][0] for e in http.posted], "posted_at": http.posted_at,
            "vision": runner.stats()["vision"], "terminals": runner.evidence.terminals}  # fmt: skip


def replayed(monkeypatch, readers: int) -> dict:
    monkeypatch.setattr(lanes, "VISION_READERS", readers)
    loop = VirtualLoop()
    try:
        return loop.run_until_complete(replay())
    finally:
        loop.close()


def test_the_recorded_load_is_what_the_fixture_says():
    assert len(READS) == 1755 and sum(failed for *_, failed in READS) == 129
    assert [a for a, *_ in READS] == sorted(a for a, *_ in READS)
    assert max(seconds for _, seconds, _ in READS) < VISION_TIMEOUT_S  # no read is cancelled in the replay


def test_the_default_readers_keep_up_with_the_recorded_load_in_frame_order(monkeypatch):
    result = replayed(monkeypatch, default_readers(monkeypatch))
    assert result["max_wait"] <= VISION_TIMEOUT_S, (result["p95_wait"], result["max_wait"])
    ok = [f"fr-{n}" for n, (*_, failed) in enumerate(READS, 1) if not failed]
    assert result["posted"] == ok  # every recognition posted once, in frame order
    times = [result["posted_at"][f] for f in ok]
    assert times == sorted(times)
    assert result["vision"] == {"received": 1755, "done": 1626, "skipped": 0, "stale": 0, "failed": 129,
                                "in_flight": 0, "waiting": 0}  # fmt: skip
    failed = [f"fr-{n}" for n, (*_, f) in enumerate(READS, 1) if f]
    assert [result["terminals"][f]["result"] for f in failed] == ["read_error"] * 129


def test_four_readers_back_up_on_the_same_load(monkeypatch):
    result = replayed(monkeypatch, 4)
    assert result["max_wait"] > VISION_TIMEOUT_S, (result["p95_wait"], result["max_wait"])
    assert result["vision"]["done"] == 1626 and result["vision"]["failed"] == 129


def test_the_replay_is_deterministic(monkeypatch):
    readers = default_readers(monkeypatch)
    first, second = replayed(monkeypatch, readers), replayed(monkeypatch, readers)
    assert first["waits"] == second["waits"] and first["posted"] == second["posted"]
