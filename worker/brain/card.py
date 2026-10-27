"""Live product cards: product_seen opens a draft, the seller's spoken price makes it live (C2, C4, C7).

- A new product (`is_new`) opens a draft from the catalog match. The previous card, if it never got a price,
  is retired, so a quick switch (< 3 s) never merges two items (C7). A product already on a card is reopened
  as a new version of that card instead of a second card.
- A final `speech` applies to the card that is current when it arrives. Spark reads price, sizes and colours
  in the background (D-018; the rule reader in worker/voice/vn.py when there is no key or the call fails).
  A price makes the card live. A card that already has a price keeps it (D-023): a different spoken price
  becomes a `price_proposal` for the seller, once per heard price while the card's price stays the same
  (D-024). Each change is a new version whose provenance lists the speech event ids (C4).
- A version another writer already took (409, D-024: the seller's price route) is applied on top of the card's
  highest version once, or dropped; the number is never reused.
- When a card first goes live and its frame is cached, SAM 3.1 finds the product in the background; the frame
  cut to SAM's box with SAM's mask as alpha (real pixels, nothing retouched, D-020) arrives as the next version's
  image_url.
- product_seen replayed from a file (no `caused_by`) opens cards only with CARDS_FROM_REPLAY=1, so a replay that
  still carries fixture cards does not get a second set (docs/DECISIONS.md D-017).
"""

import asyncio
import copy
import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.contracts import now_ms
from app.settings import ROOT, env
from meta.client import ModelError, SchemaError
from meta.meter import BudgetExceeded
from worker.brain.refused import latest_card, on_top
from worker.runner import build
from worker.voice import vn

log = logging.getLogger("worker.card")
CATALOG: list[dict] = json.loads((ROOT / "data" / "catalog.json").read_text())
CATEGORIES = sorted({i["category"] for i in CATALOG})
COLORS = sorted({v["color"] for i in CATALOG for v in i["variants"]})
CATEGORY_VI = {
    "bag": "Túi",
    "belt": "Thắt lưng",
    "boot": "Boot",
    "dress": "Váy",
    "hat": "Mũ",
    "heels": "Giày cao gót",
    "jacket": "Áo khoác",
    "jeans": "Quần jean",
    "loafer": "Giày lười",
    "sandal": "Sandal",
    "shirt": "Áo sơ mi",
    "skirt": "Chân váy",
    "sneaker": "Giày sneaker",
    "sweater": "Áo len",
    "trousers": "Quần tây",
    "tshirt": "Áo thun",
}
CARD_FIELDS = (
    "card_id",
    "version",
    "sku",
    "name",
    "price",
    "currency",
    "variants",
    "stock",
    "image_url",
    "status",
    "provenance",
)
SPEECH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["price_vnd", "sizes", "colors"],
    "properties": {
        "price_vnd": {"type": ["integer", "null"]},
        "sizes": {"type": "array", "items": {"type": "string"}},
        "colors": {"type": "array", "items": {"type": "string"}},
    },
}


def keywords() -> list[str]:
    """Catalog product names, for the transcriber's keyword list."""
    return [i["name"] for i in CATALOG]


def match(category: str, color: str | None) -> dict | None:
    """The one catalog item of this category (and colour, when seen); None when it is not unique."""
    same = [i for i in CATALOG if i["category"] == category]
    if color:
        same = [i for i in same if any(v["color"] == color for v in i["variants"])]
    return same[0] if len(same) == 1 else None


def item_by_sku(sku: str | None) -> dict | None:
    return next((i for i in CATALOG if i["sku"] == sku), None)


def variants_of(item: dict | None, color: str | None, sizes: list[str] | None = None) -> list[dict]:
    if not item:
        return []
    out = [dict(v) for v in item["variants"] if not color or v["color"] == color] or [
        dict(v) for v in item["variants"]
    ]
    if sizes:
        out = [v for v in out if v["size"] in sizes] or out
    return out


@dataclass
class Session:
    cards: dict[str, dict] = field(default_factory=dict)  # card_id -> latest version (card fields only)
    meta: dict[str, dict] = field(default_factory=dict)  # card_id -> category, color, frame ts and event id
    current: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tasks: set = field(default_factory=set)
    reading: dict[str, int] = field(default_factory=dict)  # card_id -> speech reads in flight
    proposed: dict[str, set[int]] = field(
        default_factory=dict
    )  # card_id -> heard prices proposed at its price

    def bump(self, card_id: str, **changes) -> dict:
        card = {**copy.deepcopy(self.cards[card_id]), **changes}
        card["version"] += 1
        self.cards[card_id] = card
        return copy.deepcopy(card)

    def follow(self, card: dict) -> None:
        """Take a version written elsewhere; a new price reopens proposals for every heard price."""
        cid = card["card_id"]
        if card["price"] != self.cards[cid]["price"]:
            self.proposed.pop(cid, None)
        self.cards[cid] = {k: copy.deepcopy(card[k]) for k in CARD_FIELDS}

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)


SESSIONS: dict[str, Session] = {}


def card_id_for(trigger_id: str, taken: dict) -> str:
    """card-<x> from the product_seen id (`<target>.ps-a` -> card-a), unique within the session (D-001)."""
    tail = re.sub(r"^(ps-|product_seen_)", "", trigger_id.rsplit(".", 1)[-1])[:12] or "x"
    cid, n = f"card-{tail}", 2
    while cid in taken:
        cid, n = f"card-{tail}-{n}", n + 1
    return cid


def partial(card: dict, infer: tuple[int, int], caused_by: list[str] | None = None) -> dict:
    out = {"type": "card", **card, "ts_ms": now_ms(), "_infer": infer}
    if caused_by:
        out["caused_by"] = caused_by
    return out


async def on_product_seen(event: dict, ctx) -> list[dict]:
    if not event.get("is_new") or not (event.get("caused_by") or env("CARDS_FROM_REPLAY") == "1"):
        return []
    start = now_ms()
    s = SESSIONS.setdefault(event["session_id"], Session())
    category, color = event["category"], (event.get("attributes") or {}).get("color")
    item = match(category, color)
    out = []
    async with s.lock:
        prev = s.cards.get(s.current) if s.current else None
        same = item and next(
            (c for c in s.cards.values() if c["sku"] == item["sku"] and c["status"] != "retired"), None
        )
        if prev and prev["status"] == "draft" and prev is not same and not s.reading.get(prev["card_id"]):
            out.append(s.bump(prev["card_id"], status="retired"))
        if same:
            prov = {**same["provenance"], "frame_ts_ms": event["frame_ts_ms"]}
            card = s.bump(same["card_id"], provenance=prov)
        else:
            cid = card_id_for(event["event_id"], s.cards)
            variants = variants_of(item, color)
            card = {
                "card_id": cid,
                "version": 1,
                "sku": item["sku"] if item else None,
                "name": item["name"]
                if item
                else f"{CATEGORY_VI.get(category, category)} {color or ''}".strip(),
                "price": None,
                "currency": "VND",
                "variants": variants,
                "stock": sum(v["stock"] for v in variants),
                "image_url": None,
                "status": "draft",
                "provenance": {
                    "frame_ts_ms": event["frame_ts_ms"],
                    "speech_event_ids": [],
                    "edited_by": None,
                },
            }
            s.cards[cid] = copy.deepcopy(card)
        s.meta[card["card_id"]] = {
            "category": category,
            "color": color,
            "frame_ts_ms": event["frame_ts_ms"],
            "frame_event_id": (event.get("caused_by") or [None])[0],
        }
        s.current = card["card_id"]
        out.append(card)
    infer = (start, now_ms())
    return [partial(c, infer) for c in out]


def speech_input(text: str, card: dict) -> list[dict]:
    prompt = (
        "Người bán đang livestream bán hàng. Thẻ sản phẩm hiện tại: "
        f"{card['name']} (giá hiện tại: {card['price'] or 'chưa có'}).\n"
        f'Câu người bán vừa nói: "{text}"\n'
        "Trích giá bán của món này bằng VND, số nguyên (ví dụ 'bốn trăm rưỡi' = 450000); null nếu câu không "
        "nói giá. sizes: các size được nhắc, viết như '38' hoặc 'M'. colors: các màu được nhắc, tiếng Việt "
        f"thường, chọn trong {COLORS}. Không đoán thứ câu không nói."
    )
    return [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}]


async def read_speech(text: str, card: dict, ctx) -> tuple[vn.Spoken, tuple[int, int]]:
    """Spark extraction; the rule reader when there is no key, the budget is spent or the output is bad."""
    try:
        res = await ctx.client().responses(
            speech_input(text, card), SPEECH_SCHEMA, purpose="card_from_speech", name="card_from_speech"
        )
        d = res.data
        price = (
            d["price_vnd"] if isinstance(d["price_vnd"], int) and 1000 <= d["price_vnd"] <= 10**8 else None
        )
        colors = [c for c in d["colors"] if c in COLORS]
        return vn.Spoken(price, [str(x).upper() for x in d["sizes"]], colors), (
            res.infer_start_ms,
            res.infer_end_ms,
        )
    except (RuntimeError, SchemaError, ModelError, BudgetExceeded) as exc:
        log.info("card_from_speech: rule reader (%s)", exc)
    start = now_ms()
    return vn.parse(text, set(COLORS)), (start, now_ms())


async def on_speech(event: dict, ctx) -> list[dict]:
    """The card is the current one when the speech arrives; Spark reads it in the background so a slow reply
    (5-8 s measured) never holds up the next product_seen (D-009)."""
    s = SESSIONS.get(event["session_id"])
    if not event.get("final") or not s or not s.current or s.cards[s.current]["status"] == "retired":
        return []
    cid = s.current
    s.reading[cid] = s.reading.get(cid, 0) + 1
    s.spawn(apply_speech(event, ctx, s, cid))
    return []


async def apply_speech(event: dict, ctx, s: Session, cid: str) -> None:
    try:
        spoken, infer = await read_speech(event["text"], s.cards[cid], ctx)
    except Exception as exc:  # not raised by the handler any more, so log it here
        log.exception("card_from_speech for %s failed: %s", cid, exc)
        spoken, infer = vn.Spoken(None, [], []), (now_ms(), now_ms())
    finally:
        s.reading[cid] -= 1
    proposal, new = None, None
    async with s.lock:
        card, meta = s.cards[cid], s.meta[cid]
        if card["status"] == "retired":
            return
        changes = {}
        if spoken.price is not None and card["price"] is None:
            changes["price"] = spoken.price
        elif spoken.price is not None and spoken.price != card["price"]:
            # a priced card keeps its price; a different spoken price is a proposal for the seller (D-024)
            log.info(
                "price proposal: card %s v%s price %s heard %s from %s",
                cid,
                card["version"],
                card["price"],
                spoken.price,
                event["event_id"],
            )
            heard = s.proposed.setdefault(cid, set())
            if spoken.price not in heard:
                heard.add(spoken.price)
                proposal = {
                    "type": "price_proposal",
                    "card_id": cid,
                    "card_version": card["version"],
                    "current_price": card["price"],
                    "heard_price": spoken.price,
                    "speech_event_id": event["event_id"],
                    "_infer": infer,
                }  # ts_ms and caused_by: the speech's
        if spoken.sizes and card["sku"]:
            color = spoken.colors[0] if len(spoken.colors) == 1 else meta["color"]
            variants = variants_of(item_by_sku(card["sku"]), color, spoken.sizes)
            if variants != card["variants"]:
                changes["variants"], changes["stock"] = variants, sum(v["stock"] for v in variants)
        went_live = card["status"] == "draft" and changes.get("price") is not None
        if went_live:
            changes["status"] = "live"
        elif card["status"] == "draft" and s.current != cid and not s.reading[cid]:
            changes["status"] = "retired"  # switched away while this was read and still no price
        if changes:
            ids = [*card["provenance"]["speech_event_ids"], event["event_id"]]
            changes["provenance"] = {**card["provenance"], "speech_event_ids": ids}
            new = s.bump(cid, **changes)
    if proposal:
        await ctx.runner.post([build(event, proposal, ctx.received_ms)])
    if new is None:
        return
    new = await post_version(event, ctx, s, new, changes, infer)
    if went_live and new and not new["image_url"]:
        await attach_cutout(event, ctx, s, cid)


async def attach_cutout(event: dict, ctx, s: Session, cid: str) -> None:
    """SAM 3.1 cut-out of the card's frame -> next version with image_url (caused by the speech and frame)."""
    from worker.vision import frames, sam

    meta = s.meta[cid]
    frame = frames.get(event["session_id"], meta["frame_ts_ms"])
    if not frame:
        return
    try:
        res = await sam.cut_out(ctx.client(), frame, meta["category"])
    except Exception as exc:  # the card stays without an image; the stream goes on (C7)
        log.warning("sam cut-out for %s failed: %s", cid, exc)
        return
    if not res:
        return
    url, infer = res
    async with s.lock:
        if s.cards[cid]["status"] == "retired" or s.cards[cid]["image_url"]:
            return
        new = s.bump(cid, image_url=url)
    extra = [meta["frame_event_id"]] if meta["frame_event_id"] else []
    await post_version(event, ctx, s, new, {"image_url": url}, infer, extra)


async def post_version(event, ctx, s: Session, new: dict, changes: dict, infer, extra=None) -> dict | None:
    """Post a bumped version. On 409 another writer has the number (D-024): read the card's highest version,
    apply `changes` on top of it once, or drop them. Returns the version stored, or None."""
    cid = new["card_id"]
    for attempt in (1, 2):
        try:
            await ctx.runner.post([build(event, partial(new, infer, extra), ctx.received_ms)])
            return new
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            log.info(
                "card %s v%s refused (409, attempt %s): %s", cid, new["version"], attempt, exc.response.text
            )
        if attempt == 2:
            break
        latest = await latest_card(ctx, event["session_id"], cid)
        async with s.lock:
            if latest and latest["version"] >= s.cards[cid]["version"]:
                s.follow(latest)
            rest = on_top(s.cards[cid], changes)
            if not rest:
                log.info("card %s: nothing left to apply on v%s, dropped", cid, s.cards[cid]["version"])
                return None
            new = s.bump(cid, **rest)
    log.warning("card %s v%s refused twice, dropped", cid, new["version"])
    return None


async def on_card(event: dict, ctx) -> list[dict]:
    """Follow versions made elsewhere (console edits) so the next version number is right."""
    s = SESSIONS.get(event["session_id"])
    cid = event["card_id"]
    if s and cid in s.cards and event["version"] > s.cards[cid]["version"]:
        s.follow(event)
    return []


HANDLERS = {"product_seen": [on_product_seen], "speech": [on_speech], "card": [on_card]}
