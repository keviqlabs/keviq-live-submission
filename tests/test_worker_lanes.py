"""worker/runner lanes: a slow vision read never holds up comments, speech or orders (RC1).

The stream's frames go to one vision job per session plus one waiting slot (a newer frame replaces the waiting one,
which is skipped and counted); every other event goes to the fast lane, one at a time in arrival order. The
runner's counts add up: done + skipped + stale + failed + in_flight + waiting = received, in each lane.
"""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx

from worker import runner as lanes
from worker.runner import Runner

OUTCOMES = ("done", "skipped", "stale", "failed", "in_flight", "waiting")


def frame(n: int, ts_ms: int, session: str = "s") -> dict:
    return {"event_id": f"fr-{n}", "type": "frame", "session_id": session, "ts_ms": ts_ms, "source": "replay"}


def comment(n: int, session: str = "s") -> dict:
    return {"event_id": f"cm-{n}", "type": "comment", "session_id": session, "ts_ms": 0, "source": "replay"}


class Http:
    """The worker's stream: each event at its time (seconds after the stream opened), then it holds like a follow
    stream with nothing new. Posts are kept; `refuse` names triggers whose consequences the app refuses."""

    def __init__(self, timed: list[tuple[float, dict]], refuse: frozenset[str] = frozenset()):
        self.timed, self.refuse, self.posted = timed, refuse, []

    @asynccontextmanager
    async def stream(self, method, url, params=None):
        yield self

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        loop = asyncio.get_running_loop()
        t0, timed, self.timed = loop.time(), self.timed, []
        for seq, (at, event) in enumerate(timed, 1):
            await asyncio.sleep(max(0.0, t0 + at - loop.time()))
            for line in (f"id: {seq}", f"data: {json.dumps(event)}", ""):
                yield line
        await asyncio.Event().wait()

    async def post(self, url, json=None):
        if json and json[0]["caused_by"][0] in self.refuse:
            request = httpx.Request("POST", url)
            raise httpx.HTTPStatusError(
                "refused", request=request, response=httpx.Response(500, request=request)
            )
        self.posted += json
        return httpx.Response(200, request=httpx.Request("POST", url))


class Log:
    """What the handlers saw, with the loop time each one started and finished."""

    def __init__(self):
        self.started, self.ended = {}, {}

    def handler(self, seconds: float, out: list[dict] | None = None):
        async def handle(event, ctx):
            loop = asyncio.get_running_loop()
            self.started[event["event_id"]] = loop.time()
            await asyncio.sleep(seconds)
            self.ended[event["event_id"]] = loop.time()
            return out or []

        return handle


async def running(runner: Runner, seconds: float, at: list[float] = ()) -> list[dict]:
    """Run the worker for `seconds`; the runner's counts at each time in `at`, and at the end."""
    work = asyncio.create_task(runner.run(cursor=0))
    loop = asyncio.get_running_loop()
    t0, seen = loop.time(), []
    for t in [*at, seconds]:
        await asyncio.sleep(max(0.0, t0 + t - loop.time()))
        seen.append(runner.stats())
    work.cancel()
    return seen


def adds_up(stats: dict) -> bool:
    return all(
        sum(stats[kind][k] for k in OUTCOMES) == stats[kind]["received"] for kind in ("fast", "vision")
    )


async def test_a_slow_frame_read_does_not_hold_up_the_comments_behind_it():
    log = Log()
    http = Http([(0.0, frame(1, 1000)), (0.05, comment(1)), (0.1, comment(2))])
    runner = Runner("http://app", {"frame": [log.handler(0.6)], "comment": [log.handler(0.0)]}, http=http)
    t0 = asyncio.get_running_loop().time()
    [stats] = await running(runner, 0.9)
    assert log.ended["cm-1"] - t0 < 0.3 and log.ended["cm-2"] - t0 < 0.3  # not after the 0.6 s read
    assert log.ended["fr-1"] - t0 >= 0.6
    assert stats["fast"]["done"] == 2 and stats["vision"]["done"] == 1 and adds_up(stats)


async def test_a_newer_frame_replaces_the_waiting_one_which_is_skipped_and_counted():
    log = Log()
    frames = [
        (0.1 * i, frame(i + 1, 1000 * (i + 1))) for i in range(5)
    ]  # 5 frames in 0.4 s; a read takes 0.5 s
    runner = Runner("http://app", {"frame": [log.handler(0.5)]}, http=Http(frames))
    [mid, end] = await running(runner, 1.3, at=[0.45])
    assert sorted(log.started, key=log.started.get) == ["fr-1", "fr-5"]  # the newest frame, never a backlog
    assert mid["vision"] == {"received": 5, "done": 0, "skipped": 3, "stale": 0, "failed": 0, "in_flight": 1,
                             "waiting": 1}  # fmt: skip
    assert end["vision"] == {"received": 5, "done": 2, "skipped": 3, "stale": 0, "failed": 0, "in_flight": 0,
                             "waiting": 0}  # fmt: skip


async def test_a_frame_not_newer_than_the_last_read_is_stale_and_not_read():
    log = Log()
    late = [(0.0, frame(1, 2000)), (0.2, frame(2, 1000)), (0.3, frame(1, 2000))]  # older, then the same again
    runner = Runner("http://app", {"frame": [log.handler(0.05)]}, http=Http(late))
    [stats] = await running(runner, 0.5)
    assert list(log.started) == ["fr-1"]
    assert (stats["vision"]["done"], stats["vision"]["stale"]) == (1, 2) and adds_up(stats)


async def test_each_session_has_its_own_vision_job():
    log = Log()
    two = [(0.0, frame(1, 1000, "a")), (0.05, frame(2, 1000, "b"))]
    runner = Runner("http://app", {"frame": [log.handler(0.4)]}, http=Http(two))
    t0 = asyncio.get_running_loop().time()
    [stats] = await running(runner, 0.6)
    assert log.started["fr-2"] - t0 < 0.2  # not after a's read
    assert stats["vision"]["done"] == 2


async def test_the_fast_lane_keeps_arrival_order_one_event_at_a_time():
    log = Log()
    comments = [(0.0, comment(n)) for n in range(1, 6)]
    slow_first = {"comment": [log.handler(0.05)]}
    runner = Runner("http://app", slow_first, http=Http(comments))
    [stats] = await running(runner, 0.5)
    order = sorted(log.started, key=log.started.get)
    assert order == [f"cm-{n}" for n in range(1, 6)]
    pairs = zip(order, order[1:], strict=False)
    assert all(log.started[b] >= log.ended[a] for a, b in pairs)  # never overlapping
    assert stats["fast"]["done"] == 5


async def test_a_refused_post_is_a_failed_event_and_the_lane_goes_on():
    log = Log()
    out = [{"type": "product_seen", "category": "bag", "attributes": {"color": None, "size": None, "material": None},
            "confidence": 0.9, "is_new": True, "frame_ts_ms": 1000}]  # fmt: skip
    timed = [(0.0, frame(1, 1000)), (0.2, frame(2, 2000)), (0.25, comment(1))]
    runner = Runner("http://app", {"frame": [log.handler(0.05, out)], "comment": [log.handler(0.0)]},
                    http=Http(timed, refuse=frozenset({"fr-1"})))  # fmt: skip
    [stats] = await running(runner, 0.5)
    assert list(log.started) == ["fr-1", "fr-2", "cm-1"]
    assert stats["vision"] == {"received": 2, "done": 1, "skipped": 0, "stale": 0, "failed": 1, "in_flight": 0,
                               "waiting": 0}  # fmt: skip
    assert [e["caused_by"][0] for e in runner.http.posted] == ["fr-2"]
    assert stats["fast"]["done"] == 1 and adds_up(stats)


async def test_a_read_past_the_time_limit_is_failed_once_and_the_next_frame_is_read_at_once(monkeypatch):
    """A vision call that hangs is cancelled at VISION_TIMEOUT_S: its frame is failed, never read again, and the
    lane takes the frame waiting behind it right away."""
    monkeypatch.setattr(lanes, "VISION_TIMEOUT_S", 0.2, raising=False)
    log = Log()
    hangs, quick = log.handler(5.0), log.handler(0.0)

    async def read(event, ctx):
        return await (hangs if event["event_id"] == "fr-1" else quick)(event, ctx)

    runner = Runner(
        "http://app", {"frame": [read]}, http=Http([(0.0, frame(1, 1000)), (0.05, frame(2, 2000))])
    )
    t0 = asyncio.get_running_loop().time()
    work = asyncio.create_task(runner.run(cursor=0))
    try:
        await asyncio.sleep(0.6)
        assert list(log.started) == ["fr-1", "fr-2"] and "fr-1" not in log.ended  # read once, cancelled
        assert 0.2 <= log.started["fr-2"] - t0 < 0.4
        assert runner.stats()["vision"] == {"received": 2, "done": 1, "skipped": 0, "stale": 0, "failed": 1,
                                            "in_flight": 0, "waiting": 0}  # fmt: skip
    finally:
        work.cancel()
