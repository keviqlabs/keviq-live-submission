"""Console API for orders: the seller's one-tap decision, the source log (C6), and the simulated Messenger webhook.

No seller identity yet: anyone who can reach the console can decide. Fine for the local demo, not for a public
URL or live test level 2.
"""

import asyncio
import hashlib

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.attachment_auth import require_console
from app.attachments_api import request_body
from app.contracts import ContractError, make_event, now_ms
from connectors.sim.messenger import SimMessenger, parse_webhook
from orders import flow

router = APIRouter()
MESSENGER = SimMessenger()
LOCK = asyncio.Lock()  # one decision at a time, so two taps cannot both leave NEEDS_SELLER


class Decision(BaseModel):
    decision: str
    unit_price: int | None = None
    card_id: str | None = None


async def session_events(store, session_id: str) -> list[dict]:
    out, after = [], 0
    while rows := await store.read(session_id, after, 500):
        out.extend(rows)
        after = rows[-1]["seq"]
    return out


async def append(store, session_id: str, source: str, partials: list[dict], received_ms: int) -> list[dict]:
    events = []
    for p in partials:
        p = dict(p)
        etype, ts = p.pop("type"), p.pop("ts_ms")
        timing = {"received_ms": received_ms, "emitted_ms": now_ms()}
        try:
            events.append(make_event(etype, session_id, ts, source, timing=timing, **p))
        except ContractError as exc:
            raise HTTPException(500, str(exc)) from exc
    for e in events:
        await store.append(e)
    return events


@router.post("/api/sessions/{session_id}/orders/{order_id}/decision")
async def decide(session_id: str, order_id: str, body: Decision, request: Request):
    """Seller approves (at the card price, or at `unit_price`) or rejects an order waiting in NEEDS_SELLER."""
    store, received = request.app.state.store, now_ms()
    require_console(request.headers.get("authorization"))
    if body.unit_price is not None and body.unit_price < 0:
        raise HTTPException(409, "a negative price cannot be approved")
    intent = await request_body(request, "order-decision")
    if await store.attachment_binding(session_id) is not None:
        from app.attachment_orders import decide as attachment_order_decide

        async with store.session_transaction(session_id):
            events = await attachment_order_decide(store, session_id, order_id, intent, now_ms())
        return {"events": events}
    if "card_id" in intent:
        raise HTTPException(422, "card choice is not applicable to a legacy order")
    async with LOCK:
        view = flow.View.of(await session_events(store, session_id))
        order = view.order(order_id)
        if order is None:
            raise HTTPException(404, f"no order {order_id} in {session_id}")
        try:
            partials = await flow.decide(view, order_id, body.decision, MESSENGER, now_ms(), body.unit_price)
        except flow.FlowError as exc:
            raise HTTPException(409, str(exc)) from exc
        events = await append(store, session_id, order["source"], partials, received)
    return {"events": events}


@router.get("/api/sessions/{session_id}/orders/{order_id}/log")
async def order_log(session_id: str, order_id: str, request: Request):
    """Where the order came from: comment, label, card version, every decision and who made it, messages."""
    log = flow.order_log(flow.View.of(await session_events(request.app.state.store, session_id)), order_id)
    if log is None:
        raise HTTPException(404, f"no order {order_id} in {session_id}")
    return log


@router.post("/api/sessions/{session_id}/sim/messenger")
async def sim_messenger(session_id: str, request: Request):
    """Simulated customer messages as a Page webhook body -> message_in (source sim). Refused in a live session."""
    require_console(request.headers.get("authorization"))
    store = request.app.state.store
    events = await session_events(store, session_id)
    if any(e["source"] == "facebook" for e in events):
        raise HTTPException(403, "session has Facebook events: simulated customer messages are refused")
    partials = []
    for m in parse_webhook(await request.json()):
        mid = flow.safe(f"mi.{m.pop('mid') or m['ts_ms']}")
        if len(mid) > 96:
            mid = "mi.h" + hashlib.sha1(mid.encode()).hexdigest()
        partials.append({**m, "event_id": mid})
    if not partials:
        raise HTTPException(422, "no customer message in the webhook body")
    return {"events": await append(store, session_id, "sim", partials, now_ms())}
