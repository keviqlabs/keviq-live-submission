"""Worker handlers for the orders stream: comment_labeled(order) -> draft + rules; message_in -> confirm / cancel.

Each handler reads the session so far from the app (`GET /api/sessions/{sid}/events?follow=false`) and hands it
to orders/flow.py; the runner posts what comes back (D-003: the app is the only writer). Fixture triggers and
fixture orders are left alone, so a replay with fixtures shows the recorded orders unchanged.
"""

from connectors.sim.messenger import SimMessenger
from orders import flow
from worker.runner import parse_sse

MESSENGER = SimMessenger()


async def load_view(ctx, session_id: str) -> flow.View:
    events = []
    async with ctx.runner.http.stream(
        "GET", f"/api/sessions/{session_id}/events", params={"cursor": 0, "follow": "false"}
    ) as resp:
        resp.raise_for_status()
        async for _, event in parse_sse(resp.aiter_lines()):
            events.append(event)
    return flow.View.of(events)


async def on_labeled(event: dict, ctx) -> list[dict]:
    if event.get("label") != "order" or flow.is_fixture(event["event_id"]):
        return []
    view = await load_view(ctx, event["session_id"])
    return await flow.on_labeled(event, view, MESSENGER, ctx.received_ms)


async def on_message(event: dict, ctx) -> list[dict]:
    kind, _ = flow.customer_reply(event.get("template"), event.get("payload") or {})
    if kind is None:
        return []
    view = await load_view(ctx, event["session_id"])
    return await flow.on_reply(event, view, MESSENGER, ctx.received_ms)


HANDLERS = {"comment_labeled": [on_labeled], "message_in": [on_message]}
