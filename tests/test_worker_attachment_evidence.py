"""Worker receipts do not invent an observed cursor or treat discarded frames as completed reads."""

import asyncio
import json
from types import SimpleNamespace

import httpx

from worker.runner import Runner


async def test_registered_card_preloads_history_before_a_comment_batch_without_a_second_read():
    from worker.brain.labels import Labeler

    started, release = asyncio.Event(), asyncio.Event()

    class Stored(Endpoint):
        def stream(self, *args, **kwargs):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def response():
                self.reads += 1
                started.set()
                await release.wait()
                yield httpx.Response(200, text="", request=httpx.Request("GET", "http://test/events"))

            return response()

    endpoint = Stored()
    runner = worker(endpoint)
    labeler = Labeler(catalog=[])
    card = {"session_id": "synthetic", "card_id": "card", "version": 1, "event_id": "card-v1"}
    try:
        await labeler.on_card(card, SimpleNamespace(runner=runner))
        await asyncio.wait_for(started.wait(), 1)
        assert endpoint.reads == 1  # No comment or label batch has run.
        batch_read = asyncio.create_task(labeler._versions("synthetic", runner))
        await asyncio.sleep(0)
        assert not batch_read.done()
        release.set()
        assert await batch_read == [card]
        assert endpoint.reads == 1
    finally:
        release.set()
        await asyncio.gather(*labeler.tasks, return_exceptions=True)


class Endpoint:
    def __init__(self):
        self.requests = []
        self.reads = 0

    async def get(self, url, **kwargs):
        self.reads += 1
        return httpx.Response(200, text="", request=httpx.Request("GET", "http://test/events"))

    async def post(self, url, **kwargs):
        self.requests.append(kwargs["json"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"pending": True})


def worker(http):
    return Runner(
        "http://test",
        {},
        http=http,
        attachment_auth={"synthetic": {"epoch": "epoch", "source_token": "source", "system_token": "system"}},
    )


async def test_posted_card_does_not_advance_the_actual_stream_cursor():
    endpoint = Endpoint()
    runner = worker(endpoint)
    evidence = runner.evidence
    try:
        for event in [
            {"type": "frame", "event_id": "frame", "ts_ms": 10},
            {"type": "comment", "event_id": "comment", "comment_id": "intent", "ts_ms": 15},
            {"type": "frame", "event_id": "after", "ts_ms": 20},
            {"type": "comment_labeled", "event_id": "label", "comment_id": "intent", "label": "order"},
        ]:
            evidence.receive({**event, "session_id": "synthetic"})
        seen = {"type": "product_seen", "event_id": "seen", "session_id": "synthetic", "caused_by": ["frame"]}
        card = {"type": "card", "event_id": "card", "session_id": "synthetic", "caused_by": ["seen"]}
        runner.cursor = 5
        evidence.posted([seen, card], [{"event_id": "seen", "seq": 5}, {"event_id": "card", "seq": 6}])
        evidence.product_finished(seen)
        await evidence.task
        assert not endpoint.requests and runner.cursor == 5 and endpoint.reads == 1
        runner.cursor = 6
        evidence.receive(card)
        await evidence.task
        assert len(endpoint.requests) == 1 and endpoint.requests[0]["observed_cursor"] == 6
    finally:
        await evidence.close()


async def test_read_through_advances_only_observed_prefix_without_dispatching_work():
    card = {"type": "card", "event_id": "card", "session_id": "synthetic", "caused_by": ["seen"]}

    class Stored(Endpoint):
        async def get(self, url, **kwargs):
            self.reads += 1
            return httpx.Response(
                200,
                text="id: 6\ndata: " + json.dumps(card) + "\n\n",
                request=httpx.Request("GET", "http://test/events"),
            )

        async def post(self, url, **kwargs):
            self.requests.append(kwargs["json"])
            return SimpleNamespace(
                raise_for_status=lambda: None, json=lambda: {"attachment": {"outcome": "ready"}}
            )

    endpoint = Stored()
    runner = worker(endpoint)
    evidence = runner.evidence
    try:
        for event in [
            {"type": "frame", "event_id": "frame", "ts_ms": 10},
            {"type": "comment", "event_id": "comment", "comment_id": "intent", "ts_ms": 15},
            {"type": "frame", "event_id": "after", "ts_ms": 20},
            {"type": "comment_labeled", "event_id": "label", "comment_id": "intent", "label": "order"},
        ]:
            evidence.receive({**event, "session_id": "synthetic"})
        seen = {"type": "product_seen", "event_id": "seen", "session_id": "synthetic", "caused_by": ["frame"]}
        runner.cursor = 5
        evidence.posted([seen, card], [{"event_id": "seen", "seq": 5}, {"event_id": "card", "seq": 6}])
        evidence.product_finished(seen)
        await evidence.task
        assert endpoint.reads == 1 and len(endpoint.requests) == 1
        assert endpoint.requests[0]["observed_cursor"] == 6 and runner.cursor == 5
        assert runner.counts["fast"]["received"] == runner.counts["vision"]["received"] == 0
        assert list(evidence.terminals) == ["frame"]
    finally:
        await evidence.close()


async def test_own_label_post_still_requires_reading_its_stored_prefix_before_proof():
    seen = {"type": "product_seen", "event_id": "seen", "session_id": "synthetic", "caused_by": ["frame"]}
    card = {"type": "card", "event_id": "card", "session_id": "synthetic", "caused_by": ["seen"]}
    label = {
        "type": "comment_labeled",
        "event_id": "label",
        "session_id": "synthetic",
        "comment_id": "intent",
        "label": "order",
    }

    class Stored(Endpoint):
        async def get(self, url, **kwargs):
            self.reads += 1
            text = "".join(
                f"id: {seq}\ndata: {json.dumps(event)}\n\n"
                for seq, event in [(4, seen), (5, card), (6, label)]
            )
            return httpx.Response(200, text=text, request=httpx.Request("GET", "http://test/events"))

        async def post(self, url, **kwargs):
            self.requests.append(kwargs["json"])
            return SimpleNamespace(
                raise_for_status=lambda: None, json=lambda: {"attachment": {"outcome": "ready"}}
            )

    endpoint = Stored()
    runner = worker(endpoint)
    evidence = runner.evidence
    try:
        for event in [
            {"type": "frame", "event_id": "frame", "ts_ms": 10},
            {"type": "comment", "event_id": "comment", "comment_id": "intent", "ts_ms": 15},
            {"type": "frame", "event_id": "after", "ts_ms": 20},
        ]:
            evidence.receive({**event, "session_id": "synthetic"})
        runner.cursor = 3
        evidence.posted(
            [seen, card, label],
            [{"event_id": "seen", "seq": 4}, {"event_id": "card", "seq": 5}, {"event_id": "label", "seq": 6}],
        )
        evidence.product_finished(seen)
        await evidence.task
        assert endpoint.reads == 1 and len(endpoint.requests) == 1
        assert endpoint.requests[0]["observed_cursor"] == 6 and runner.cursor == 3
        assert runner.counts["fast"]["received"] == 0
    finally:
        await evidence.close()


async def test_late_external_recognition_cannot_overwrite_canonical_terminal_receipt():
    runner = worker(Endpoint())
    evidence = runner.evidence
    first = {"type": "frame", "event_id": "frame20", "session_id": "synthetic", "ts_ms": 20}
    seen = {
        "type": "product_seen",
        "event_id": "canonical20",
        "session_id": "synthetic",
        "caused_by": ["frame20"],
    }
    card = {"type": "card", "event_id": "card20", "session_id": "synthetic", "caused_by": ["canonical20"]}
    try:
        evidence.receive(first)
        evidence.posted(
            [seen, card], [{"event_id": "canonical20", "seq": 2}, {"event_id": "card20", "seq": 3}]
        )
        evidence.product_finished(seen)
        before = dict(evidence.terminals["frame20"])
        evidence.receive({**first, "event_id": "frame30", "ts_ms": 30})
        evidence.product_finished({**seen, "event_id": "late20"})
        assert evidence.terminals["frame20"] == before
    finally:
        await evidence.close()


async def settled(runner, seconds=1.0):
    """Until the vision lanes are empty and the evidence ledger has evaluated what they left."""
    loop = asyncio.get_running_loop()
    until = loop.time() + seconds
    while loop.time() < until:
        vision, task = runner.stats()["vision"], runner.evidence.task
        if vision["in_flight"] == vision["waiting"] == 0 and (task is None or task.done()):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the lanes did not settle")


async def test_a_replaced_frame_in_a_bound_session_blocks_readiness_until_it_is_read():
    """Three frames arrive while the first is still being read, with an order comment between the second and the
    third: readiness needs terminals for frames 1 and 2 and frame 3 as the frame after. A lane that replaces the
    waiting frame 2 with frame 3 leaves frame 2 without a terminal, so the comment can never be proven ready;
    a bound session's lane reads every frame instead."""
    endpoint = Endpoint()
    runner = worker(endpoint)
    started, release = asyncio.Event(), asyncio.Event()

    async def read(event, ctx):
        if event["event_id"] == "fr-1":
            started.set()
            await release.wait()
        return []

    runner.handlers = {"frame": [read]}
    frame = {"type": "frame", "session_id": "synthetic", "source": "sim"}
    fast = {"session_id": "synthetic", "source": "sim", "comment_id": "intent"}
    try:
        runner.receive({**frame, "event_id": "fr-1", "ts_ms": 10})
        await asyncio.wait_for(started.wait(), 1)
        runner.receive({**frame, "event_id": "fr-2", "ts_ms": 20})
        runner.receive({**fast, "type": "comment", "event_id": "comment", "ts_ms": 25})
        runner.receive({**frame, "event_id": "fr-3", "ts_ms": 30})
        label = {**fast, "type": "comment_labeled", "event_id": "label", "ts_ms": 25, "label": "order"}
        runner.receive(label)
        release.set()
        await settled(runner)
        readiness = [r for r in endpoint.requests if "readiness_evidence" in r]
        assert (runner.counts["vision"]["skipped"], len(readiness)) == (0, 1)
        assert sorted(runner.evidence.terminals) == ["fr-1", "fr-2", "fr-3"]
        evidence = readiness[0]["readiness_evidence"]
        assert evidence["after_frame_event_id"] == "fr-3"
        assert [f["frame_event_id"] for f in evidence["handled_frames"]] == ["fr-1", "fr-2"]
        assert {f["result"] for f in evidence["handled_frames"]} == {"no_product_seen"}
    finally:
        release.set()
        await settled(runner)
        await runner.evidence.close()


async def test_refused_product_post_is_not_a_read_error_completion():
    class Refused:
        async def post(self, url, **kwargs):
            response = httpx.Response(409, request=httpx.Request("POST", "http://test/events"))
            response.raise_for_status()

    runner = worker(Refused())
    event = {"type": "frame", "event_id": "frame", "session_id": "synthetic", "ts_ms": 10, "source": "sim"}

    async def seen(trigger, ctx):
        return [
            {
                "type": "product_seen",
                "category": "sneaker",
                "attributes": {},
                "confidence": 1,
                "is_new": True,
                "frame_ts_ms": trigger["ts_ms"],
            }
        ]

    runner.handlers = {"frame": [seen]}
    runner.evidence.receive(event)
    try:
        assert await runner.process(event, 10) == "failed"
        assert "frame" not in runner.evidence.terminals
    finally:
        await runner.evidence.close()
