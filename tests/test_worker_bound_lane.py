"""worker/runner lanes for a session with an attachment binding: every frame is read, several at a time.

An order comment in a bound session is proven ready only with a terminal for each frame up to it, so its lane
never replaces a waiting frame. Reads run up to VISION_READERS at a time; their results are posted in media time
order, a read's post waiting for the read taken before it. An unbound session's lane still keeps the latest frame.
The counts still add up: done + skipped + stale + failed + in_flight + waiting = received.
"""

import asyncio

import httpx

from tests.test_worker_lanes import Http, Log, adds_up, frame, running
from worker import runner as lanes
from worker.runner import Runner

AUTH = {"b": {"epoch": "epoch", "source_token": "source", "system_token": "system"}}


def bound(n: int, ts_ms: int) -> dict:
    return frame(n, ts_ms, session="b")


class BoundHttp(Http):
    """The stream of test_worker_lanes for a bound session too: posts carry the source token and come back accepted
    with sequence numbers, the history read is empty and a readiness request is pending. Each post's loop time is
    kept by trigger."""

    def __init__(self, timed, refuse=frozenset()):
        super().__init__(timed, refuse)
        self.seq, self.readiness, self.posted_at = 0, [], {}

    async def get(self, url, **kwargs):
        return httpx.Response(200, text="", request=httpx.Request("GET", url))

    async def post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        if url.endswith("/attachments/system"):
            self.readiness.append(json)
            return httpx.Response(200, json={"pending": True}, request=request)
        await super().post(url, json)
        accepted = []
        for event in json:
            self.seq += 1
            accepted.append({"event_id": event["event_id"], "seq": self.seq})
            self.posted_at[event["caused_by"][0]] = asyncio.get_running_loop().time()
        return httpx.Response(200, json={"accepted": accepted}, request=request)


def seen(ts_ms: int) -> list[dict]:
    return [{"type": "product_seen", "category": "bag", "attributes": {"color": None, "size": None, "material": None},
             "confidence": 0.9, "is_new": True, "frame_ts_ms": ts_ms}]  # fmt: skip


async def test_a_bound_session_reads_every_frame_up_to_the_readers_at_a_time(monkeypatch):
    monkeypatch.setattr(lanes, "VISION_READERS", 2)
    log = Log()
    # 5 frames in 0.2 s; a read takes 0.3 s
    burst = [(0.05 * i, bound(i + 1, 1000 * (i + 1))) for i in range(5)]
    runner = Runner("http://app", {"frame": [log.handler(0.3)]}, http=BoundHttp(burst), attachment_auth=AUTH)
    [mid, later, end] = await running(runner, 1.2, at=[0.25, 0.45])
    assert sorted(log.started, key=log.started.get) == [f"fr-{n}" for n in range(1, 6)]  # all, in media order
    assert mid["vision"] == {"received": 5, "done": 0, "skipped": 0, "stale": 0, "failed": 0, "in_flight": 2,
                             "waiting": 3}  # fmt: skip
    assert later["vision"] == {"received": 5, "done": 2, "skipped": 0, "stale": 0, "failed": 0, "in_flight": 2,
                               "waiting": 1}  # fmt: skip
    assert end["vision"] == {"received": 5, "done": 5, "skipped": 0, "stale": 0, "failed": 0, "in_flight": 0,
                             "waiting": 0}  # fmt: skip
    assert all(adds_up(stats) for stats in (mid, later, end))


async def test_a_read_that_finishes_before_an_earlier_one_posts_after_it(monkeypatch):
    """Two reads at a time: the second frame's quick read is posted only once the first frame's slow read has been,
    so cards appear in media time order."""
    monkeypatch.setattr(lanes, "VISION_READERS", 2)
    log = Log()
    slow, quick = log.handler(0.4, seen(1000)), log.handler(0.05, seen(2000))

    async def read(event, ctx):
        return await (slow if event["event_id"] == "fr-1" else quick)(event, ctx)

    http = BoundHttp([(0.0, bound(1, 1000)), (0.05, bound(2, 2000))])
    runner = Runner("http://app", {"frame": [read]}, http=http, attachment_auth=AUTH)
    t0 = asyncio.get_running_loop().time()
    [stats] = await running(runner, 0.7)
    assert log.ended["fr-2"] - t0 < 0.3 < 0.4 <= log.ended["fr-1"] - t0  # the second read finished first
    assert [e["caused_by"][0] for e in http.posted] == ["fr-1", "fr-2"]
    assert http.posted_at["fr-2"] >= http.posted_at["fr-1"] >= log.ended["fr-1"]
    assert stats["vision"]["done"] == 2 and adds_up(stats)


async def test_a_read_past_the_time_limit_is_a_read_error_and_the_next_frame_posts_after_it(monkeypatch):
    monkeypatch.setattr(lanes, "VISION_READERS", 2)
    monkeypatch.setattr(lanes, "VISION_TIMEOUT_S", 0.2, raising=False)
    log = Log()
    hangs, quick = log.handler(5.0), log.handler(0.0, seen(2000))

    async def read(event, ctx):
        return await (hangs if event["event_id"] == "fr-1" else quick)(event, ctx)

    http = BoundHttp([(0.0, bound(1, 1000)), (0.05, bound(2, 2000))])
    runner = Runner("http://app", {"frame": [read]}, http=http, attachment_auth=AUTH)
    t0 = asyncio.get_running_loop().time()
    [stats] = await running(runner, 0.6)
    assert list(log.started) == ["fr-1", "fr-2"] and "fr-1" not in log.ended  # read once, cancelled
    assert log.ended["fr-2"] - t0 < 0.15
    assert 0.2 <= http.posted_at["fr-2"] - t0 < 0.4  # posted once fr-1 was failed
    assert runner.evidence.terminals["fr-1"]["result"] == "read_error"
    assert "fr-2" not in runner.evidence.terminals  # a recognition is terminal only once its cards are posted
    assert stats["vision"] == {"received": 2, "done": 1, "skipped": 0, "stale": 0, "failed": 1, "in_flight": 0,
                               "waiting": 0}  # fmt: skip


async def test_an_unbound_session_still_keeps_the_latest_frame(monkeypatch):
    monkeypatch.setattr(lanes, "VISION_READERS", 2)
    log = Log()
    timed = sorted(
        [(0.1 * i, frame(i + 1, 1000 * (i + 1))) for i in range(5)]
        + [(0.1 * i + 0.01, bound(i + 11, 1000 * (i + 1))) for i in range(5)]
    )  # 5 frames each in 0.4 s; a read takes 0.5 s
    runner = Runner("http://app", {"frame": [log.handler(0.5)]}, http=BoundHttp(timed), attachment_auth=AUTH)
    [stats] = await running(runner, 1.8)
    started, unbound = sorted(log.started, key=log.started.get), {f"fr-{n}" for n in range(1, 6)}
    assert [f for f in started if f in unbound] == ["fr-1", "fr-5"]
    assert [f for f in started if f not in unbound] == [f"fr-{n}" for n in range(11, 16)]
    assert stats["vision"] == {"received": 10, "done": 7, "skipped": 3, "stale": 0, "failed": 0, "in_flight": 0,
                               "waiting": 0}  # fmt: skip


async def test_a_frame_not_newer_than_the_last_taken_is_stale_in_a_bound_lane_too(monkeypatch):
    monkeypatch.setattr(lanes, "VISION_READERS", 2)
    log = Log()
    late = [(0.0, bound(1, 2000)), (0.2, bound(2, 1000)), (0.3, bound(1, 2000))]  # older, then the same again
    runner = Runner("http://app", {"frame": [log.handler(0.05)]}, http=BoundHttp(late), attachment_auth=AUTH)
    [stats] = await running(runner, 0.5)
    assert list(log.started) == ["fr-1"]
    assert (stats["vision"]["done"], stats["vision"]["stale"]) == (1, 2) and adds_up(stats)
