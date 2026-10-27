"""worker/vision: owned by its stream. Register handlers here: {event_type: [async fn(event, ctx) -> list[dict]]}."""

from worker.vision.spark import see_product

HANDLERS: dict = {"frame": [see_product]}
