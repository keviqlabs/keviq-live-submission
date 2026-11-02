"""frame -> product_seen: Spark reads one frame (strict json_schema) and says what product the seller is showing.

Only a change of product is emitted (`is_new` true): a new category, or the same category in a new colour,
compared with the last product this session saw. Frames with no product, or the same product again, produce
nothing. A product_seen gets a card version within the cycle (D-009): a new card, or, when two reads in flight
both report the same change, a version of the kept card that moves its frame (worker/brain/card.py). One of a
frame older than the last frame the card brain took is ignored there as stale and gets no version.
The frame is cached for the SAM cut-out.
A read that fails (no key, model error, budget spent, output failed twice) fails the frame in the runner's vision
lane; it is not read again.
"""

import logging
from dataclasses import dataclass

from meta.client import SchemaError
from worker.brain.card import CATEGORIES, COLORS, kind_words, other_identity
from worker.runner import Failed
from worker.vision import frames

log = logging.getLogger("worker.vision")
MIN_CONFIDENCE = 0.5
# Lowest effort Muse Spark accepts. Measured with this prompt on the 351 test1 frames, ledger p95 3168 ms:
# run probe-test1-6b-1950, docs/evidence/2026-10-27/vision-test1-6b-score-1027-1950.json.
EFFORT = "minimal"
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
    kind: str | None = None
    identity_state: str | None = None


LAST: dict[str, Seen] = {}
_WARNED: set[tuple[str, str]] = set()


def frame_input(jpeg: bytes, last: Seen | None) -> list[dict]:
    before = f"{last.category} màu {last.color or 'không rõ'}" if last else "chưa có"
    prompt = (
        "Đây là một khung hình từ livestream bán hàng. Món người bán đang cầm hoặc giới thiệu là gì? "
        "category chọn trong danh sách; danh sách không đầy đủ: món không có trong danh sách (ví dụ đồ điện "
        "tử, đồ gia dụng, phụ kiện) thì chọn 'other'; chỉ chọn 'none' khi người bán không cầm và không giới "
        "thiệu món nào. Quần áo người bán đang mặc trên người không phải sản phẩm đang giới thiệu. "
        f"color: tiếng Việt thường, chọn trong {COLORS} nếu khớp. size/material: chỉ khi nhìn thấy rõ, "
        f"không thì null. Món thấy ở khung trước: {before}. Nếu món đang cầm vẫn là món ở khung trước thì "
        "giữ đúng category và color của khung trước. confidence: mức chắc chắn 0..1."
    )
    kinds, colors = kind_words()
    if kinds:
        prompt += (
            f" Với category='other', kind chọn loại thật sự nhìn rõ trong {kinds}, không rõ thì null. "
            f"Màu trong danh mục: {colors}; chỉ dùng khi khớp màu quan sát, không ép màu theo danh mục. "
            "identity_state='one_product' khi thấy rõ một món; 'unclear' khi bị che, mờ, đang đổi món hoặc "
            "không rõ loại/màu. Không đoán theo món trước. Với category khác, kind=null."
        )
        if last and last.category == "other":
            prompt += f" Loại quan sát trước: {last.kind}; trạng thái trước: {last.identity_state}."
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
    kinds, _ = kind_words()
    schema = SCHEMA
    if kinds:
        schema = {
            **SCHEMA,
            "required": [*SCHEMA["required"], "kind", "identity_state"],
            "properties": {
                **SCHEMA["properties"],
                "kind": {"type": ["string", "null"], "enum": [*kinds, None]},
                "identity_state": {"type": "string", "enum": ["one_product", "unclear"]},
            },
        }
    return await client.responses(
        frame_input(jpeg, last), schema, purpose="product_seen", name="product_seen", reasoning=EFFORT
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
        raise Failed(str(exc)) from exc
    d = res.data
    if not d["has_product"] or d["category"] == "none":
        return []
    color = d["color"].strip().lower() if isinstance(d["color"], str) and d["color"].strip() else None
    identity = {}
    if d["category"] == "other" and kind_words()[0]:
        _, identity = other_identity({**d, "color": color}, d["confidence"])
    elif d["confidence"] < MIN_CONFIDENCE:
        return []
    seen = Seen(d["category"], color, **identity)
    is_new = (
        last is None
        or seen.category != last.category
        or bool(color and last.color and color != last.color)
        or (seen.kind, seen.identity_state) != (last.kind, last.identity_state)
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
            "attributes": {"color": color, "size": d["size"], "material": d["material"], **identity},
            "confidence": round(float(d["confidence"]), 3),
            "is_new": True,
            "frame_ts_ms": event["ts_ms"],
            "_infer": (res.infer_start_ms, res.infer_end_ms),
        }
    ]
