"""Worker runner: consume the global SSE stream, call handlers by event type, post consequence events.

A handler is `async def h(event, ctx) -> list[dict]`. Each returned dict is a partial event: `type` plus that
type's fields, optionally `ts_ms` (default: the trigger's ts_ms), extra `caused_by` ids, and `_infer` =
(infer_start_ms, infer_end_ms). The runner fills the envelope, puts the trigger first in `caused_by` (C1/C6)
and stamps `timing` (C8). A handler that raises is logged and skipped; the runner keeps going (C7).

Lanes (RC1): a slow vision read never holds up comments, speech or orders. Frames go to their session's vision
lane: one read at a time, within VISION_TIMEOUT_S, and one frame waiting; a newer frame takes the waiting one's
place (that one is skipped), a frame not newer than the last one read or the one waiting is stale, so results
are posted in media time order. Every other event goes to the fast lane, one at a time in arrival order. Each
event leaves its lane once: done, skipped, stale or failed (a handler failed or timed out, or the post was
refused; nothing is retried). stats() has the counts per lane and the work handlers keep in the background.

The stream cursor moves when an event is put in its lane, not when it is finished: a worker that stops with
events in its lanes resumes after them and they are lost.
"""

import asyncio
import copy
import json
import logging
from collections import Counter, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from app.contracts import make_event, new_id, now_ms

log = logging.getLogger("worker")
Handler = Callable[[dict, "Ctx"], Awaitable[list[dict]]]
SLOW = frozenset({"frame"})  # event types read by the vision model: the vision lanes
VISION_TIMEOUT_S = 10.0  # a frame's handlers past it are cancelled and the frame is failed
OUTCOMES = ("done", "skipped", "stale", "failed")
# name -> {waiting, in_flight}: work handlers keep in the background (registered by their modules)
BACKLOGS: dict[str, Callable[[], dict]] = {}


class Failed(Exception):
    """Raised by a handler that has logged its own failure: the event is failed, with no second log line."""


@dataclass
class Ctx:
    event: dict
    runner: "Runner"
    received_ms: int = field(default_factory=now_ms)

    def client(self):
        """MetaClient whose model_call events go to this event's session, caused by this event."""
        return self.runner.client_for(self.event)


def build(trigger: dict, partial: dict, received_ms: int) -> dict:
    p = dict(partial)
    etype = p.pop("type")
    infer = p.pop("_infer", None)
    timing = {"received_ms": received_ms, "emitted_ms": now_ms()}
    if infer:
        timing["infer_start_ms"], timing["infer_end_ms"] = infer
    caused_by = [trigger["event_id"], *[c for c in p.pop("caused_by", []) if c != trigger["event_id"]]]
    return make_event(
        etype,
        trigger["session_id"],
        p.pop("ts_ms", trigger["ts_ms"]),
        trigger["source"],
        event_id=p.pop("event_id", new_id(etype)),
        caused_by=caused_by,
        timing=timing,
        **p,
    )


async def parse_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[int, dict]]:
    seq, data = None, []
    async for line in lines:
        if line.startswith("id:"):
            seq = int(line[3:].strip())
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "" and data:
            yield seq, json.loads("".join(data))
            seq, data = None, []


class Lane:
    """Events handled one at a time, each with the time the runner received it. A lane that keeps the latest (a
    session's frames) holds one waiting: a newer frame takes its place, an older or equal one is stale. Its task
    runs only while it has work."""

    def __init__(self, runner: "Runner", kind: str, keep_latest: bool = False, timeout: float | None = None):
        self.runner, self.kind, self.keep_latest, self.timeout = runner, kind, keep_latest, timeout
        self.waiting: deque[tuple[dict, int]] = deque()
        self.in_flight = 0
        self.last_ts: int | None = None  # ts_ms of the last frame taken to read
        self.task: asyncio.Task | None = None

    def put(self, event: dict, received_ms: int) -> None:
        if self.keep_latest:
            newest = self.waiting[-1][0]["ts_ms"] if self.waiting else self.last_ts
            if newest is not None and event["ts_ms"] <= newest:
                self.runner.release(self, event, "stale")
                return
            if self.waiting:
                self.runner.release(self, self.waiting.pop()[0], "skipped")
        self.waiting.append((event, received_ms))
        if self.task is None:
            self.task = asyncio.create_task(self.work())

    async def work(self) -> None:
        try:
            while self.waiting:
                event, received_ms = self.waiting.popleft()
                if self.keep_latest:
                    self.last_ts = event["ts_ms"]
                self.in_flight += 1
                try:
                    outcome = await self.runner.process(event, received_ms, self.timeout)
                finally:
                    self.in_flight -= 1
                self.runner.release(self, event, outcome)
        finally:
            self.task = None


class Runner:
    KINDS = ("fast", "vision")  # lane kinds

    def __init__(
        self,
        base_url: str,
        handlers: dict[str, list[Handler]],
        http: httpx.AsyncClient | None = None,
        *,
        attachment_auth=None,
    ):
        self.base_url, self.handlers = base_url, handlers
        self.http = http or httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(10, read=None))
        self.cursor = 0
        self.failures = 0
        self._meta = None
        self.fast = Lane(self, "fast")
        self.vision: dict[str, Lane] = {}  # session_id -> its vision lane
        self.counts = {kind: Counter(dict.fromkeys(("received", *OUTCOMES), 0)) for kind in self.KINDS}
        self.attachment_auth = copy.deepcopy(attachment_auth or {})
        self._attachment_sessions = {}
        from worker.attachments import FrameEvidence

        self.evidence = FrameEvidence(self, self.attachment_auth)

    async def attachment_enabled(self, sid):
        if sid in self.attachment_auth:
            return True
        if sid not in self._attachment_sessions:
            response = await self.http.get(f"/api/sessions/{sid}/state")
            response.raise_for_status()
            self._attachment_sessions[sid] = response.json().get("attachment_enabled", False)
        return self._attachment_sessions[sid]

    def client_for(self, event: dict):
        from meta.client import MetaClient
        from meta.meter import Meter, http_session_spend, http_spend
        from meta.transport import HttpTransport

        if self._meta is None:
            meter = Meter(http_spend(self.base_url), session_spend=http_session_spend(self.base_url))
            self._meta = (HttpTransport(), meter)
        transport, meter = self._meta

        async def emit(fields: dict) -> None:
            await self.post([build(event, {"type": "model_call", **fields}, now_ms())])

        return MetaClient(transport, meter, emit, session_id=event["session_id"])

    async def post(self, events: list[dict]) -> None:
        if not events:
            return
        sid = events[0]["session_id"]
        headers = (
            {"Authorization": "Bearer " + self.attachment_auth[sid]["source_token"]}
            if sid in self.attachment_auth
            else {}
        )
        r = await self.http.post(f"/api/sessions/{sid}/events", json=events, headers=headers)
        r.raise_for_status()
        self.evidence.posted(events, r.json()["accepted"] if sid in self.attachment_auth else None)

    async def consequences(self, event: dict, received_ms: int | None = None) -> tuple[list[dict], int]:
        """The event's handlers in order: the consequence events they return, built, and how many failed."""
        out, failed = [], 0
        for handler in self.handlers.get(event["type"], []):
            ctx = Ctx(event, self) if received_ms is None else Ctx(event, self, received_ms)
            try:
                partials = await handler(event, ctx) or []
                out.extend(build(event, p, ctx.received_ms) for p in partials)
            except Failed:
                failed += 1
            except Exception as exc:
                failed += 1
                self.failures += 1
                log.exception(
                    "handler %s failed on %s: %s",
                    getattr(handler, "__name__", handler),
                    event["event_id"],
                    exc,
                )
        return out, failed

    async def handle(self, event: dict) -> list[dict]:
        out, _ = await self.consequences(event)
        await self.post(out)
        return out

    async def process(self, event: dict, received_ms: int, limit_s: float | None = None) -> str:
        """One event of a lane: its handlers, all within `limit_s` (cancelled past it), then the post of what they
        returned. Its outcome: done or failed."""
        try:
            out, failed = await asyncio.wait_for(self.consequences(event, received_ms), limit_s)
        except TimeoutError:
            log.warning("handlers of %s cancelled after %s s", event["event_id"], limit_s)
            if event["type"] == "frame":
                self.evidence.frame_finished(event, [], read_error=True)
            return "failed"
        try:
            await self.post(out)
        except Exception as exc:  # refused, or the app is unreachable: these consequences are lost (C7)
            log.exception("posting the consequences of %s failed: %s", event["event_id"], exc)
            return "failed"
        if event["type"] == "frame":
            self.evidence.frame_finished(event, out, read_error=bool(failed))
        elif event["type"] == "product_seen" and not failed:
            self.evidence.product_finished(event)
        return "failed" if failed else "done"

    def receive(self, event: dict) -> None:
        """Put a stream event in its lane: a frame in its session's vision lane, any other event in the fast lane."""
        self.evidence.receive(event)
        if event["type"] in SLOW:
            sid = event["session_id"]
            if sid not in self.vision:
                self.vision[sid] = Lane(self, "vision", keep_latest=True, timeout=VISION_TIMEOUT_S)
            lane = self.vision[sid]
        else:
            lane = self.fast
        self.counts[lane.kind]["received"] += 1
        lane.put(event, now_ms())

    def release(self, lane: Lane, event: dict, outcome: str) -> None:
        """The event leaves its lane with its outcome (OUTCOMES)."""
        self.counts[lane.kind][outcome] += 1
        if outcome in ("skipped", "stale"):
            log.info(
                "frame %s %s (session %s: %d skipped, %d stale)",
                event["event_id"],
                outcome,
                event["session_id"],
                self.counts[lane.kind]["skipped"],
                self.counts[lane.kind]["stale"],
            )

    def stats(self) -> dict:
        """Per lane kind: events received and what became of them (done + skipped + stale + failed + in_flight +
        waiting = received); then each of BACKLOGS: what handlers keep waiting and in flight in the background."""
        out = {}
        for kind, lanes in (("fast", [self.fast]), ("vision", list(self.vision.values()))):
            out[kind] = {**self.counts[kind], "in_flight": sum(lane.in_flight for lane in lanes),
                         "waiting": sum(len(lane.waiting) for lane in lanes)}  # fmt: skip
        return out | {name: backlog() for name, backlog in BACKLOGS.items()}

    async def run(self, cursor: int | None = None, session: str | None = None) -> None:
        if cursor is None:
            cursor = (await self.http.get("/healthz")).json()["head_seq"]
        self.cursor = cursor
        path = f"/api/sessions/{session}/events" if session else "/api/events"
        log.info(
            "worker: consuming %s from seq %s (%d event types handled)", path, cursor, len(self.handlers)
        )
        try:
            while True:
                try:
                    async with self.http.stream("GET", path, params={"cursor": self.cursor}) as resp:
                        resp.raise_for_status()
                        async for seq, event in parse_sse(resp.aiter_lines()):
                            self.cursor = seq or self.cursor
                            self.receive(event)
                            await asyncio.sleep(0)  # the lanes run between stream lines
                except (httpx.HTTPError, OSError) as exc:
                    log.warning("worker: stream dropped (%s); resuming from seq %s", exc, self.cursor)
                    await asyncio.sleep(1)
        finally:
            await self.evidence.close()
            for lane in [self.fast, *self.vision.values()]:
                if lane.task:
                    lane.task.cancel()
