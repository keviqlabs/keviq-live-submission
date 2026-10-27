"""HTTP API: ingest, SSE with cursor resume, session state, console, overlay and demo pages."""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.contracts import ContractError, now_ms, validate_event
from app.orders_api import router as orders_router
from app.settings import ROOT, database_url, env_float
from app.store import TEST_SESSION_PREFIXES, live_cards_from, make_store, session_state, vn_day_bounds

log = logging.getLogger("app")
STATIC = ROOT / "app" / "static"
KEEPALIVE_S = 15.0
BATCH = 500


def create_app(store=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store or make_store(database_url())
        await app.state.store.open()
        if app.state.store.kind == "memory":
            log.warning("DATABASE_URL not set: using the in-memory store, nothing is persisted")
        yield
        await app.state.store.close()

    app = FastAPI(title="keviq-live-event", lifespan=lifespan)
    app.include_router(orders_router)

    def st(request: Request):
        return request.app.state.store

    @app.get("/healthz")
    async def healthz(request: Request):
        return {"ok": True, "store": st(request).kind, "head_seq": await st(request).head_seq()}

    @app.post("/api/sessions/{session_id}/events")
    async def ingest(session_id: str, request: Request):
        payload = await request.json()
        events = payload if isinstance(payload, list) else [payload]
        out = []
        for event in events:
            try:
                validate_event(event)
            except ContractError as exc:
                raise HTTPException(422, str(exc)) from exc
            if event["session_id"] != session_id:
                raise HTTPException(
                    400, f"event {event['event_id']} belongs to session {event['session_id']}"
                )
            seq, inserted = await st(request).append(event)
            out.append({"event_id": event["event_id"], "seq": seq, "duplicate": not inserted})
        return {"accepted": out}

    async def stream(request: Request, session_id: str | None, cursor: int, follow: bool):
        store = st(request)
        while True:
            token = store.notifier.token()
            rows = await store.read(session_id, cursor, BATCH)
            for e in rows:
                cursor = e["seq"]
                data = json.dumps(e, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {cursor}\nevent: {e['type']}\ndata: {data}\n\n"
            if len(rows) == BATCH:
                continue
            if not follow:
                return
            if await request.is_disconnected():
                return
            if not await store.notifier.wait(token, KEEPALIVE_S):
                yield ": keepalive\n\n"

    def sse(request, session_id, cursor, follow, last_event_id):
        if last_event_id and last_event_id.isdigit():
            cursor = max(cursor, int(last_event_id))
        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(
            stream(request, session_id, cursor, follow), media_type="text/event-stream", headers=headers
        )

    @app.get("/api/sessions/{session_id}/events")
    async def session_events(
        session_id: str,
        request: Request,
        cursor: int = 0,
        follow: bool = True,
        last_event_id: str | None = Header(None),
    ):
        """SSE: every event of the session with seq > cursor, then live. `id:` is the cursor to resume from."""
        return sse(request, session_id, cursor, follow, last_event_id)

    @app.get("/api/events")
    async def all_events(
        request: Request, cursor: int = 0, follow: bool = True, last_event_id: str | None = Header(None)
    ):
        """SSE over every session (used by the worker)."""
        return sse(request, None, cursor, follow, last_event_id)

    @app.get("/api/sessions")
    async def sessions(request: Request):
        return {"sessions": await st(request).sessions()}

    @app.get("/api/sessions/{session_id}/state")
    async def state(session_id: str, request: Request):
        return await session_state(st(request), session_id)

    @app.get("/api/sessions/{session_id}/cards/live")
    async def live_cards(session_id: str, request: Request, ts_ms: int):
        """Cards live at ts_ms (latest version at or before ts_ms, status live/edited), newest card first."""
        return {"ts_ms": ts_ms, "cards": live_cards_from(await st(request).card_versions(session_id), ts_ms)}

    @app.get("/api/cost")
    async def cost(request: Request):
        """Spend the caps check (all model_call rows), plus the part caused by test sessions, shown apart."""
        start, end = vn_day_bounds(now_ms())
        store = st(request)
        return {
            "today_usd": round(await store.cost_usd(start, end), 6),
            "total_usd": round(await store.cost_usd(), 6),
            "daily_cap_usd": env_float("MODEL_DAILY_CAP_USD", 30),
            "daily_warn_usd": env_float("MODEL_DAILY_WARN_USD", 20),
            "total_cap_usd": env_float("MODEL_TOTAL_CAP_USD", 300),
            "test_sessions": {
                "prefixes": list(TEST_SESSION_PREFIXES),
                "today_usd": round(await store.cost_usd(start, end, test_only=True), 6),
                "total_usd": round(await store.cost_usd(test_only=True), 6),
            },
        }

    @app.get("/")
    async def demo():
        return FileResponse(STATIC / "index.html")

    @app.get("/console/{session_id}")
    async def console(session_id: str):
        return FileResponse(STATIC / "console.html")

    @app.get("/overlay/{session_id}")
    async def overlay(session_id: str):
        return FileResponse(STATIC / "overlay.html")

    @app.get("/static/{name}")
    async def static(name: str):
        path = (STATIC / name).resolve()
        if path.parent != STATIC.resolve() or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    return app


app = create_app()
