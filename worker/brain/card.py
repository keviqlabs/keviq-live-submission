"""Live product cards: product_seen opens a draft, the seller's spoken price makes it live (C2, C4, C7).

- A new product (`is_new`) opens a draft from the catalog match and a media span on that card (D-025). The
  previous card, if it never got a price and no undecided speech line overlaps it, is retired, so a quick switch
  (< 3 s) never merges two items (C7). A known card is reopened only when its observation still agrees and the
  current appearance has not lost identity. A changed coarse observation opens an independent unknown draft.
  The same category and colour again while the current card has no SKU is that card, not a new one: two reads in
  flight together both report the change from the item before (worker/vision/spark.py), so no card is opened,
  retired or given a span for it. The kept card gets a version that only moves its frame to the repeat's
  (caused by the repeat, so the repeat has a consequence of its own, D-009); SKU, price and status stay.
  A product_seen already handled (the same event id) or of a frame older than the last one taken is ignored.
- A final `speech` goes to a card by media time, not by arrival (D-025, worker/brain/window.py). Spark reads
  price, sizes and colours in the background (D-018; the rule reader in worker/voice/vn.py when there is no key
  or the call fails), at most SPEECH_READS lines at a time from read to decision; the others wait their turn. The
  line is decided once its read is done and HOLD_MS after it arrived, in arrival order.
  A heard price goes to the one candidate card; with several, the kind of item the line names picks one. Any
  other line leaves the price for the seller and counts as a wait by reason in the session (`price wait` log
  lines). A price makes the card live. A card that already has a price keeps it (D-023): a
  different spoken price becomes a `price_proposal` for the seller, once per heard price while the card's price
  stays the same (D-024). Each change is a new version whose provenance lists the speech event ids (C4).
- A version another writer already took (409, D-024: the seller's price route) is applied on top of the card's
  highest version once, or dropped; the number is never reused. product_seen's versions go the same way.
- When a card first goes live and its frame is cached, SAM 3.1 finds the product in the background, one card at a
  time per session, so a slow cut-out never holds up the next line's decision; the frame cut to SAM's box with
  SAM's mask as alpha (real pixels, nothing retouched, D-020) arrives as the next version's image_url.
- product_seen replayed from a file (no `caused_by`) opens cards only with CARDS_FROM_REPLAY=1, so a replay that
  still carries fixture cards does not get a second set (docs/DECISIONS.md D-017).
- runner.stats() has the lines and cut-outs waiting and in flight (BACKLOGS), so a measure barrier waits for them.
"""

import asyncio
import copy
import json
import logging
import re
from collections import Counter, deque
from dataclasses import dataclass, field

import httpx

from app.contracts import now_ms
from app.settings import ROOT, catalog_path, env
from meta.client import ModelError, SchemaError
from meta.meter import BudgetExceeded
from worker.brain import window
from worker.brain.refused import latest_card, on_top
from worker.runner import BACKLOGS, build
from worker.voice import vn

log = logging.getLogger("worker.card")
CATALOG: list[dict] = json.loads(catalog_path().read_text())  # the items on sale (ruling 417)
# Base words stay fixed; the optional other-item vision vocabulary uses explicit active-catalog kinds.
WORDS: list[dict] = json.loads((ROOT / "data" / "catalog.json").read_text())
CATEGORIES = sorted({i["category"] for i in WORDS})
COLORS = sorted({v["color"] for i in WORDS for v in i["variants"]})
SPEECH_READS = 4  # lines between their read and their decision at once, per session
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
    return [i["name"] for i in WORDS]


def match(category: str, color: str | None) -> dict | None:
    """The one catalog item of this category (and colour, when seen); None when it is not unique. An item with
    `categories` is of each of them; one without is of its `category` (ruling 417)."""
    same = [i for i in CATALOG if category in (i.get("categories") or [i["category"]])]
    if color:
        same = [i for i in same if any(v["color"] == color for v in i["variants"])]
    return same[0] if len(same) == 1 else None


def other_items() -> list[dict]:
    """Only explicit seller-catalog kinds can refine the coarse 'other' category."""
    return [
        i
        for i in CATALOG
        if "other" in (i.get("categories") or [i["category"]])
        and isinstance(i.get("kind"), str)
        and i["kind"].strip()
    ]


def kind_words() -> tuple[list[str], list[str]]:
    items = other_items()
    return sorted({i["kind"] for i in items}), sorted(
        {v["color"] for i in items for v in i["variants"] if isinstance(v.get("color"), str)}
    )


def other_identity(attributes: dict, confidence) -> tuple[dict | None, dict]:
    """Kind is visual evidence, never a price or an authority to change an existing card's SKU."""
    kind, color = attributes.get("kind"), attributes.get("color")
    kind = kind if isinstance(kind, str) else None
    same = []
    if (
        attributes.get("identity_state") == "one_product"
        and isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and 0.5 <= confidence <= 1
        and isinstance(color, str)
        and color
        and kind
    ):
        same = [
            i for i in other_items() if i["kind"] == kind and any(v["color"] == color for v in i["variants"])
        ]
    item = same[0] if len(same) == 1 else None
    return item, {"kind": kind, "identity_state": "one_product" if item else "unclear"}


def observation(meta: dict) -> tuple:
    return tuple(meta.get(k) for k in ("category", "color", "kind", "identity_state"))


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
    spans: dict[str, list[list]] = field(default_factory=dict)  # card_id -> media spans (window.py)
    lines: list["Line"] = field(default_factory=list)  # final speech lines not decided yet, in arrival order
    proposed: dict[str, set[int]] = field(
        default_factory=dict
    )  # card_id -> heard prices proposed at its price
    waits: Counter = field(default_factory=Counter)  # reason -> heard prices that waited for the seller
    seen_ids: set[str] = field(default_factory=set)  # product_seen ids handled (a stream may repeat one)
    last_frame_ts: int | None = None  # frame_ts_ms of the newest product_seen taken
    speech: deque = field(default_factory=deque)  # (event, ctx, line) of lines waiting for a read slot
    reading: int = 0  # lines between their read and their decision, at most SPEECH_READS
    cutouts: deque = field(default_factory=deque)  # (event, ctx, card_id) of live cards waiting for SAM
    cutting: bool = False  # the session's SAM consumer is running

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


@dataclass(eq=False)
class Line:
    """A final speech line waiting for its decision; `after` is the line that arrived before it."""

    event_id: str
    start: int
    end: int
    after: "Line | None"
    done: asyncio.Event = field(default_factory=asyncio.Event)


def pending(s: Session, card_id: str, but: Line | None = None) -> bool:
    """An undecided line (other than `but`) overlaps the card's spans, so it may still go to this card."""
    spans = s.spans.get(card_id, [])
    return any(window.overlaps(spans, line.start, line.end) for line in s.lines if line is not but)


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
    if event["event_id"] in s.seen_ids:
        log.info("product_seen %s handled before: ignored", event["event_id"])
        return []
    s.seen_ids.add(event["event_id"])
    if s.last_frame_ts is not None and event["frame_ts_ms"] < s.last_frame_ts:
        log.info("product_seen %s of frame %s is older than %s: stale, ignored", event["event_id"],
                 event["frame_ts_ms"], s.last_frame_ts)  # fmt: skip
        return []
    s.last_frame_ts = event["frame_ts_ms"]
    attrs = event.get("attributes") or {}
    category, color = event["category"], attrs.get("color")
    refined = category == "other" and (other_items() or "kind" in attrs or "identity_state" in attrs)
    item, identity = (
        other_identity(attrs, event.get("confidence")) if refined else (match(category, color), {})
    )
    observed = {"category": category, "color": color, **identity}
    out = []  # (version, its changes), in posting order
    async with s.lock:
        prev = s.cards.get(s.current) if s.current else None
        last = s.meta.get(prev["card_id"], {}) if prev else {}
        if (
            prev
            and prev["sku"] is None
            and prev["status"] != "retired"
            and observation(last) == observation(observed)
        ):
            # The same item read again: only the frame moves, so the repeat has its own version (C1) while the
            # SKU, price, status, span and current item stay as they are.
            log.info("product_seen %s repeats %s %s of unidentified card %s: same item", event["event_id"],
                     category, color, prev["card_id"])  # fmt: skip
            prov = {**prev["provenance"], "frame_ts_ms": event["frame_ts_ms"]}
            s.meta[prev["card_id"]] = {**last, "frame_ts_ms": event["frame_ts_ms"],
                                       "frame_event_id": (event.get("caused_by") or [None])[0]}  # fmt: skip
            out.append((s.bump(prev["card_id"], provenance=prov), {"provenance": prov}))
        else:
            candidates = reversed(s.cards.values()) if refined else s.cards.values()
            same = item and next(
                (c for c in candidates if c["sku"] == item["sku"] and c["status"] != "retired"), None
            )
            if same:
                prior = s.meta.get(same["card_id"], {})
                agrees = observation(prior) == observation(observed)
                if not agrees or (prev and prev["sku"] is None):
                    # Clear kind evidence may open a fresh unpriced identity, never restore an old price.
                    # The legacy coarse path still refuses a one-frame category jitter as before.
                    same = None
                    if not refined:
                        item = None
            if prev and prev is not same:
                window.close_span(s.spans.setdefault(prev["card_id"], []), event["frame_ts_ms"])
                if prev["status"] == "draft" and not pending(s, prev["card_id"]):
                    out.append((s.bump(prev["card_id"], status="retired"), {"status": "retired"}))
            if same:
                prov = {**same["provenance"], "frame_ts_ms": event["frame_ts_ms"]}
                card, changes = s.bump(same["card_id"], provenance=prov), {"provenance": prov}
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
                s.cards[cid], changes = copy.deepcopy(card), {}
            if not prev or prev is not same:
                window.open_span(s.spans.setdefault(card["card_id"], []), event["frame_ts_ms"])
            s.meta[card["card_id"]] = {
                **observed,
                "frame_ts_ms": event["frame_ts_ms"],
                "frame_event_id": (event.get("caused_by") or [None])[0],
            }
            s.current = card["card_id"]
            out.append((card, changes))
    infer = (start, now_ms())
    for new, changes in out:  # one at a time, outside the lock: a 409 reads the store and takes the lock
        await post_version(event, ctx, s, new, changes, infer)
    return []


def speech_input(text: str) -> list[dict]:
    """No card in the prompt: the line is read before it is known which card it goes to (D-025)."""
    prompt = (
        "Người bán đang livestream bán hàng.\n"
        f'Câu người bán vừa nói: "{text}"\n'
        "Trích giá bán được nói trong câu bằng VND, số nguyên (ví dụ 'bốn trăm rưỡi' = 450000); null nếu câu không "
        "nói giá. sizes: các size được nhắc, viết như '38' hoặc 'M'. colors: các màu được nhắc, tiếng Việt "
        f"thường, chọn trong {COLORS}. Không đoán thứ câu không nói."
    )
    return [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}]


async def read_speech(text: str, ctx) -> tuple[vn.Spoken, tuple[int, int]]:
    """Spark extraction; the rule reader when there is no key, the budget is spent or the output is bad."""
    try:
        res = await ctx.client().responses(
            speech_input(text), SPEECH_SCHEMA, purpose="card_from_speech", name="card_from_speech"
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
    """Spark reads the line in the background so a slow reply (5-8 s measured) never holds up the next
    product_seen (D-009). Lines are decided in arrival order, so the price said first is applied first."""
    s = SESSIONS.get(event["session_id"])
    if not event.get("final") or not s or not s.cards:
        return []
    line = Line(event["event_id"], event["start_ms"], event["end_ms"], s.lines[-1] if s.lines else None)
    s.lines.append(line)
    s.speech.append((event, ctx, line))
    pump_speech(s)
    return []


def pump_speech(s: Session) -> None:
    """Start the next waiting lines, in arrival order, while fewer than SPEECH_READS are being read or decided."""
    while s.speech and s.reading < SPEECH_READS:
        s.reading += 1
        event, ctx, line = s.speech.popleft()
        s.spawn(apply_speech(event, ctx, s, line))


async def apply_speech(event: dict, ctx, s: Session, line: Line) -> None:
    try:
        try:
            spoken, infer = await read_speech(event["text"], ctx)
        except Exception as exc:  # not raised by the handler any more, so log it here
            log.exception("card_from_speech for %s failed: %s", event["event_id"], exc)
            spoken, infer = vn.Spoken(None, [], []), (now_ms(), now_ms())
        # a product_seen for this line may still be read by the vision model (window.HOLD_MS)
        await asyncio.sleep(max(0, ctx.received_ms + window.HOLD_MS - now_ms()) / 1000)
        if line.after:
            await line.after.done.wait()
        await decide(event, ctx, s, line, spoken, infer)
    finally:
        if line in s.lines:
            s.lines.remove(line)
        line.done.set()
        s.reading -= 1
        pump_speech(s)


async def decide(event: dict, ctx, s: Session, line: Line, spoken: vn.Spoken, infer) -> None:
    eid = event["event_id"]
    proposal, changed, live = None, {}, None  # changed: card_id -> its changes
    async with s.lock:
        s.lines.remove(line)
        cands = {
            cid: s.meta[cid]["category"]
            for cid, c in s.cards.items()
            if c["status"] != "retired" and window.overlaps(s.spans.get(cid, []), line.start, line.end)
        }
        keep, reason = window.choose(event["text"], cands)
        if spoken.price is not None and reason:
            s.waits[reason] += 1
            log.info(
                "price wait: %s reason %s candidates %s; session %s waits %s",
                eid,
                reason,
                ",".join(cands) or "-",
                event["session_id"],
                " ".join(f"{r}={n}" for r, n in sorted(s.waits.items())),
            )
        price = None if reason else spoken.price
        if len(keep) == 1:
            cid = keep[0]
            card, meta, changes = s.cards[cid], s.meta[cid], {}
            if price is not None and card["price"] is None:
                changes["price"] = price
            elif price is not None and price != card["price"]:
                # a priced card keeps its price; a different spoken price is a proposal for the seller (D-024)
                log.info(
                    "price proposal: card %s v%s price %s heard %s from %s",
                    cid,
                    card["version"],
                    card["price"],
                    price,
                    eid,
                )
                heard = s.proposed.setdefault(cid, set())
                if price not in heard:
                    heard.add(price)
                    proposal = {
                        "type": "price_proposal",
                        "card_id": cid,
                        "card_version": card["version"],
                        "current_price": card["price"],
                        "heard_price": price,
                        "speech_event_id": eid,
                        "_infer": infer,
                    }  # ts_ms and caused_by: the speech's
            if spoken.sizes and card["sku"]:
                color = spoken.colors[0] if len(spoken.colors) == 1 else meta["color"]
                variants = variants_of(item_by_sku(card["sku"]), color, spoken.sizes)
                if variants != card["variants"]:
                    changes["variants"], changes["stock"] = variants, sum(v["stock"] for v in variants)
            if card["status"] == "draft" and changes.get("price") is not None:
                changes["status"], live = "live", cid
            if changes:
                changed[cid] = changes
        for cid in cands:  # switched away while this line was undecided, and still no price
            if cid != s.current and s.cards[cid]["status"] == "draft" and "price" not in changed.get(cid, {}):
                if not pending(s, cid):
                    changed.setdefault(cid, {})["status"] = "retired"
        news = []
        for cid, changes in changed.items():
            prov = s.cards[cid]["provenance"]
            changes["provenance"] = {**prov, "speech_event_ids": [*prov["speech_event_ids"], eid]}
            news.append((s.bump(cid, **changes), changes))
    if proposal:
        await ctx.runner.post([build(event, proposal, ctx.received_ms)])
    for new, changes in news:
        new = await post_version(event, ctx, s, new, changes, infer)
        if new and new["card_id"] == live and not new["image_url"]:
            queue_cutout(event, ctx, s, live)


def queue_cutout(event: dict, ctx, s: Session, cid: str) -> None:
    """The cut-out waits for the session's SAM consumer, which takes one card at a time."""
    s.cutouts.append((event, ctx, cid))
    if not s.cutting:
        s.cutting = True
        s.spawn(cut_out_all(s))


async def cut_out_all(s: Session) -> None:
    try:
        while s.cutouts:
            event, ctx, cid = s.cutouts.popleft()
            try:
                await attach_cutout(event, ctx, s, cid)
            except Exception as exc:  # e.g. its version refused; the card stays without an image (C7)
                log.exception("cut-out for %s failed: %s", cid, exc)
    finally:
        s.cutting = False


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
    """Post a card version. On 409 another writer has the number or the price (D-024): follow the card's highest
    version in the store, apply `changes` on top of it once, or drop them. Returns the version stored, or None.

    After a second refusal the worker keeps the refused number, so its next version skips it. A card's stored
    version numbers always rise, but they can have gaps."""
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
            if latest:  # also when it is below a number this worker holds but never stored
                s.follow(latest)
            rest = on_top(s.cards[cid], changes)
            if not rest:
                log.info("card %s: nothing left to apply on v%s, dropped", cid, s.cards[cid]["version"])
                return None
            new = s.bump(cid, **rest)
    log.warning("card %s v%s refused twice, dropped", cid, new["version"])
    return None


async def on_card(event: dict, ctx) -> list[dict]:
    """Follow versions made elsewhere (console edits) so the next version number is right. A stored version with
    the number the worker holds wins: the worker's own comes back unchanged, another writer's took the number."""
    s = SESSIONS.get(event["session_id"])
    cid = event["card_id"]
    if s and cid in s.cards and event["version"] >= s.cards[cid]["version"]:
        s.follow(event)
    return []


def speech_backlog() -> dict:
    return {
        "waiting": sum(len(s.speech) for s in SESSIONS.values()),
        "in_flight": sum(s.reading for s in SESSIONS.values()),
    }


def cutout_backlog() -> dict:
    return {
        "waiting": sum(len(s.cutouts) for s in SESSIONS.values()),
        "in_flight": sum(s.cutting for s in SESSIONS.values()),
    }


BACKLOGS["speech"], BACKLOGS["cutout"] = speech_backlog, cutout_backlog
HANDLERS = {"product_seen": [on_product_seen], "speech": [on_speech], "card": [on_card]}
