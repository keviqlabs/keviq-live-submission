"""Worker handler: a price_question or stock_question label -> a private reply about the item on screen.

A session bound to attachments is answered by the app in its reconcile pass (a source writer cannot post
message_out there), so this handler leaves it alone. Any other session: read the session so far from the app
(`GET /api/sessions/{sid}/events?follow=false`) and return what answers/message.py builds for the runner to post.
"""

from connectors.sim.messenger import SimMessenger
from worker.runner import parse_sse

from .message import QUESTION_LABELS, on_question

MESSENGER = SimMessenger()


async def load_events(ctx, session_id: str) -> list[dict]:
    events = []
    async with ctx.runner.http.stream(
        "GET", f"/api/sessions/{session_id}/events", params={"cursor": 0, "follow": "false"}
    ) as resp:
        resp.raise_for_status()
        async for _, event in parse_sse(resp.aiter_lines()):
            events.append(event)
    return events


async def on_labeled(event: dict, ctx) -> list[dict]:
    if event.get("label") not in QUESTION_LABELS:
        return []
    if hasattr(ctx.runner, "attachment_enabled") and await ctx.runner.attachment_enabled(event["session_id"]):
        return []
    events = await load_events(ctx, event["session_id"])
    partials, _ = await on_question(event, events, MESSENGER, ctx.received_ms)
    return partials


HANDLERS = {"comment_labeled": [on_labeled]}
