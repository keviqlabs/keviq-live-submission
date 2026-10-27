"""Worker runner: consume the global SSE stream, call handlers by event type, post consequence events.

A handler is `async def h(event, ctx) -> list[dict]`. Each returned dict is a partial event: `type` plus that
type's fields, optionally `ts_ms` (default: the trigger's ts_ms), extra `caused_by` ids, and `_infer` =
(infer_start_ms, infer_end_ms). The runner fills the envelope, puts the trigger first in `caused_by` (C1/C6)
and stamps `timing` (C8). A handler that raises is logged and skipped; the runner keeps going (C7).
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from app.contracts import make_event, new_id, now_ms

log = logging.getLogger("worker")
Handler = Callable[[dict, "Ctx"], Awaitable[list[dict]]]


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


class Runner:
    def __init__(
        self, base_url: str, handlers: dict[str, list[Handler]], http: httpx.AsyncClient | None = None
    ):
        self.base_url, self.handlers = base_url, handlers
        self.http = http or httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(10, read=None))
        self.cursor = 0
        self.failures = 0
        self._meta = None

    def client_for(self, event: dict):
        from meta.client import MetaClient
        from meta.meter import Meter, http_spend
        from meta.transport import HttpTransport

        if self._meta is None:
            self._meta = (HttpTransport(), Meter(http_spend(self.base_url)))
        transport, meter = self._meta

        async def emit(fields: dict) -> None:
            await self.post([build(event, {"type": "model_call", **fields}, now_ms())])

        return MetaClient(transport, meter, emit)

    async def post(self, events: list[dict]) -> None:
        if not events:
            return
        r = await self.http.post(f"/api/sessions/{events[0]['session_id']}/events", json=events)
        r.raise_for_status()

    async def handle(self, event: dict) -> list[dict]:
        out = []
        for handler in self.handlers.get(event["type"], []):
            ctx = Ctx(event, self)
            try:
                partials = await handler(event, ctx) or []
                out.extend(build(event, p, ctx.received_ms) for p in partials)
            except Exception as exc:
                self.failures += 1
                log.exception(
                    "handler %s failed on %s: %s",
                    getattr(handler, "__name__", handler),
                    event["event_id"],
                    exc,
                )
        await self.post(out)
        return out

    async def run(self, cursor: int | None = None, session: str | None = None) -> None:
        if cursor is None:
            cursor = (await self.http.get("/healthz")).json()["head_seq"]
        self.cursor = cursor
        path = f"/api/sessions/{session}/events" if session else "/api/events"
        log.info(
            "worker: consuming %s from seq %s (%d event types handled)", path, cursor, len(self.handlers)
        )
        while True:
            try:
                async with self.http.stream("GET", path, params={"cursor": self.cursor}) as resp:
                    resp.raise_for_status()
                    async for seq, event in parse_sse(resp.aiter_lines()):
                        self.cursor = seq or self.cursor
                        await self.handle(event)
            except (httpx.HTTPError, OSError) as exc:
                log.warning("worker: stream dropped (%s); resuming from seq %s", exc, self.cursor)
                await asyncio.sleep(1)
