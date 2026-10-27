"""frame -> product_seen: Spark reads one frame (strict json_schema) and says what product the seller is showing.

Only a change of product is emitted (`is_new` true): a new category, or the same category in a new colour,
compared with the last product this session saw. Frames with no product, or the same product again, produce
nothing, so every product_seen gets a card within the cycle (D-009). The frame is cached for the SAM cut-out.
"""

import logging
from dataclasses import dataclass

from meta.client import SchemaError
from worker.brain.card import CATEGORIES, COLORS
from worker.vision import frames

log = logging.getLogger("worker.vision")
MIN_CONFIDENCE = 0.5
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["has_product", "category", "color", "size", "material", "confidence"],
    "properties": {
        "has_product": {"type": "boolean"},
        "category": {"type": "string", "enum": [*CATEGORIES, "other", "none"]},
        "color": {"type": ["string", "null"]},
        "size": {"type": ["string", "null"]},
        "material": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


@dataclass
class Seen:
    category: str
    color: str | None


LAST: dict[str, Seen] = {}
_WARNED: set[tuple[str, str]] = set()


def frame_input(jpeg: bytes, last: Seen | None) -> list[dict]:
    before = f"{last.category} màu {last.color or 'không rõ'}" if last else "chưa có"
    prompt = (
        "Đây là một khung hình từ livestream bán quần áo, giày, túi. Món người bán đang cầm hoặc giới thiệu "
        f"là gì? category chọn trong danh sách; 'none' nếu không có món nào được giới thiệu. color: tiếng Việt "
        f"thường, chọn trong {COLORS} nếu khớp. size/material: chỉ khi nhìn thấy rõ, không thì null. "
        f"Món thấy ở khung trước: {before}. confidence: mức chắc chắn 0..1."
    )
    return [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": frames.data_url(jpeg)},
            ],
        }
    ]


async def read_frame(client, jpeg: bytes, last: Seen | None):
    return await client.responses(
        frame_input(jpeg, last), SCHEMA, purpose="product_seen", name="product_seen"
    )


async def see_product(event: dict, ctx) -> list[dict]:
    session = event["session_id"]
    jpeg = frames.load(event)
    frames.put(session, event["ts_ms"], jpeg)
    last = LAST.get(session)
    try:
        res = await read_frame(ctx.client(), jpeg, last)
    except (RuntimeError, SchemaError) as exc:  # no key, model error, budget spent, output failed twice
        if (session, type(exc).__name__) not in _WARNED:  # once per session and kind, not once per second
            _WARNED.add((session, type(exc).__name__))
            log.warning("product_seen skipped from %s on: %s", event["event_id"], exc)
        return []
    d = res.data
    if not d["has_product"] or d["category"] == "none" or d["confidence"] < MIN_CONFIDENCE:
        return []
    color = d["color"].strip().lower() if isinstance(d["color"], str) and d["color"].strip() else None
    seen = Seen(d["category"], color)
    is_new = (
        last is None or seen.category != last.category or bool(color and last.color and color != last.color)
    )
    if not is_new:
        if color and not last.color:
            LAST[session] = seen
        return []
    LAST[session] = seen
    return [
        {
            "type": "product_seen",
            "category": seen.category,
            "attributes": {"color": color, "size": d["size"], "material": d["material"]},
            "confidence": round(float(d["confidence"]), 3),
            "is_new": True,
            "frame_ts_ms": event["ts_ms"],
            "_infer": (res.infer_start_ms, res.infer_end_ms),
        }
    ]
