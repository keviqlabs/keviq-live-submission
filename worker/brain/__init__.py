"""worker/brain: owned by its stream. Register handlers here: {event_type: [async fn(event, ctx) -> list[dict]]}."""

from worker.brain import card

HANDLERS: dict = {}
HANDLERS.update({t: HANDLERS.get(t, []) + fns for t, fns in card.HANDLERS.items()})
HANDLERS |= {
    t: HANDLERS.get(t, []) + fns
    for t, fns in __import__("worker.brain.labels", fromlist=["*"]).HANDLERS.items()
}
