"""worker/brain/labels: comment -> comment_labeled; an order whose product cannot be pinned asks the customer.

Shield, on receipt: a phone number or a link in the text is hidden through the connector's CommentModerator
(the simulator only logs it) and a comment_labeled with action `hide` goes out at once (C5: visible <= 5 s).

Batch: every comment joins a per-session queue, labeled by the session's one consumer a batch at a time. A Spark
call labels up to 20 comments, cut when 20 are queued or the oldest has waited 1 s; the next batch is cut from
what queued meanwhile once that call is done, so a flood waits in the queue instead of opening calls at once.
runner.stats() has the comments queued and those being labeled (BACKLOGS). The model sees the catalog, the card timeline and, per comment, the current and previous
card at that comment's ts_ms. It says how the customer pointed at the product (reference_kind); code then
resolves referenced_card_id from the timeline by the comment's own ts_ms, so a late comment pins to the card
that was live when it was written (C3, C7). The batch label is final (measure keeps the latest label). A batch
whose output fails the schema twice labels its comments `other` and the stream goes on (C7).

A session carrying hand-written `fx-` cards (demo1 with fixtures) already has its labels; it is skipped.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from app.contracts import new_id, now_ms
from app.settings import catalog_path
from app.store import live_cards_from
from connectors.facebook.interface import CommentModerator
from connectors.sim.moderator import SimModerator
from orders.rules import authorize, can_message
from worker.brain.shield import first_label, scan
from worker.runner import BACKLOGS, Ctx, build, parse_sse

log = logging.getLogger("worker.labels")
BATCH_MAX, BATCH_WAIT_S = 20, 1.0
# Lowest effort Muse Spark accepts ("none" is refused, dev.meta.ai/docs/reasoning); reasoning tokens are most of
# the latency and the output bill.
EFFORT = "minimal"
LABELS = ["order", "price_question", "stock_question", "complaint", "spam", "scam", "other"]
KINDS = ["explicit_sku", "current_card", "previous_card", "unresolved"]
SIGNALS = ["phone_number", "link"]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "comment_id",
                    "label",
                    "reference_kind",
                    "sku",
                    "size",
                    "color",
                    "qty",
                    "contact",
                ],
                "properties": {
                    "comment_id": {"type": "string"},
                    "label": {"type": "string", "enum": LABELS},
                    "reference_kind": {"type": "string", "enum": [*KINDS, "none"]},
                    "sku": {"type": ["string", "null"]},
                    "size": {"type": ["string", "null"]},
                    "color": {"type": ["string", "null"]},
                    "qty": {"type": ["integer", "null"]},
                    "contact": {"type": "string", "enum": ["none", *SIGNALS]},
                },
            },
        }
    },
}

SYSTEM = """You label comments on a Vietnamese live-shopping stream. Comments are in Vietnamese or English.

label, one of:
- order: wants to buy ("chốt", "lấy", "cho mình", "đặt", "I'll take", "x1").
- price_question: asks the price. stock_question: asks about sizes, colours or availability.
- complaint: about a past order, delivery, replies or the live itself.
- spam: self-promotion, follow-for-follow, selling unrelated things, emoji floods.
- scam: prize or voucher lures, fake admin / shipping / support, asks for a transfer, OTP, bank details or a
  login, loans.
- other: anything else.

For an order, reference_kind says how the customer pointed at the product:
- previous_card: a word for earlier ("vừa rồi", "lúc nãy", "trước đó", "before", "just now") -> the comment's
  previous_card.
- explicit_sku: writes a SKU code, or names a catalog product that is not the comment's current_card; set sku.
- current_card: "this", "cái này", "đôi này", names the current card's product, or names no product at all.
- unresolved: points at something you cannot pin to one card ("hôm qua", "hôm trước", "số 5", "cái kia",
  "giống bạn ở trên"). Never guess: the shop will ask the customer.
sku: the catalog sku for explicit_sku, else null. size: as written, else null. color: only a colour the
customer wrote, as the catalog value (black -> đen, white -> trắng, red -> đỏ, brown -> nâu, grey -> xám), else
null. qty: the number written, else 1. For any other label: reference_kind "none", sku, size, color, qty null.

contact (any label): phone_number if the text holds a phone number (also one spelled out in words), else link
if it holds a link, else none. Return exactly one item per comment_id."""


def load_catalog() -> list[dict]:
    return json.loads(catalog_path().read_text())


def is_fixture(event: dict) -> bool:
    eid = event["event_id"]
    return eid.startswith("fx-") or ".fx-" in eid


def go_live_order(versions: list[dict]) -> list[dict]:
    """First version of each card, in the order the cards appeared."""
    first: dict[str, dict] = {}
    for card in sorted(versions, key=lambda c: (c["ts_ms"], c["version"])):
        first.setdefault(card["card_id"], card)
    return sorted(first.values(), key=lambda c: c["ts_ms"])


def cards_at(versions: list[dict], ts_ms: int) -> tuple[dict | None, dict | None]:
    """(current, previous) at ts_ms: current = newest live card; previous = the latest other card shown."""
    live = live_cards_from(versions, ts_ms)
    current = live[0] if live else None
    others = [
        c
        for c in go_live_order(versions)
        if c["ts_ms"] <= ts_ms and c["card_id"] != (current or {}).get("card_id")
    ]
    return current, others[-1] if others else None


def resolve(item: dict, versions: list[dict], ts_ms: int, skus: set[str]) -> dict:
    """order_draft from a model item; referenced_card_id comes from the timeline, never from the model."""
    kind, sku = item["reference_kind"], item["sku"]
    current, previous = cards_at(versions, ts_ms)
    card = None
    if kind == "current_card":
        card = current
    elif kind == "previous_card":
        card = previous
    elif kind == "explicit_sku" and sku in skus:
        shown = [c for c in go_live_order(versions) if c["sku"] == sku and c["ts_ms"] <= ts_ms]
        card = shown[-1] if shown else None
    if kind not in ("current_card", "previous_card", "explicit_sku") or (
        card is None and not (kind == "explicit_sku" and sku in skus)
    ):
        kind, sku = "unresolved", None
    elif card:
        sku = card["sku"]
    size, color = item["size"], item["color"]
    return {
        "sku": sku,
        "variant": {"size": size, "color": color} if size or color else None,
        "qty": max(item["qty"] or 1, 1),
        "referenced_card_id": card["card_id"] if card else None,
        "reference_kind": kind,
    }


def card_brief(card: dict | None) -> dict | None:
    return card and {"card_id": card["card_id"], "sku": card["sku"], "name": card["name"]}


@dataclass
class Pending:
    event: dict
    ctx: Ctx
    signals: list[str]
    action: str
    queued: float = field(default_factory=time.monotonic)


class Labeler:
    def __init__(self, client_for=None, moderator: CommentModerator | None = None, catalog=None):
        self.client_for = client_for or (lambda ctx: ctx.client())
        self.moderator = moderator or SimModerator()
        self.catalog = catalog if catalog is not None else load_catalog()
        self.skus = {c["sku"] for c in self.catalog}
        self.cards: dict[str, list[dict]] = defaultdict(list)
        self.history: dict[str, asyncio.Task] = {}
        self.fixture_sessions: set[str] = set()
        self.queues: dict[str, list[Pending]] = defaultdict(list)
        self.consumers: dict[str, asyncio.Task] = {}  # session_id -> its consumer, while it has comments
        self.full: dict[str, asyncio.Event] = {}  # session_id -> set when BATCH_MAX comments are queued
        self.labeling: dict[str, int] = defaultdict(int)  # session_id -> comments in the batch being labeled
        self.tasks: set[asyncio.Task] = set()

    def handlers(self) -> dict:
        return {"card": [self.on_card], "comment": [self.on_comment]}

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def on_card(self, event: dict, ctx: Ctx) -> list[dict]:
        sid = event["session_id"]
        if is_fixture(event):
            self.fixture_sessions.add(sid)
        known = {(c["card_id"], c["version"]) for c in self.cards[sid]}
        if (event["card_id"], event["version"]) not in known:
            self.cards[sid].append(event)
        return []

    async def on_comment(self, event: dict, ctx: Ctx) -> list[dict]:
        sid = event["session_id"]
        if sid in self.fixture_sessions:
            return []
        signals = scan(event["text"])
        action = await self._hide(event, signals) if signals else "none"
        out = []
        if signals:
            out.append(self._label(event, ctx, first_label(signals), None, signals, action))
        queue = self.queues[sid]
        queue.append(Pending(event, ctx, signals, action))
        if sid not in self.consumers:
            self.full[sid] = asyncio.Event()
            self.consumers[sid] = self._spawn(self._consume(sid))
        elif len(queue) >= BATCH_MAX:
            self.full[sid].set()
        return out

    def backlog(self) -> dict:
        return {
            "waiting": sum(len(q) for q in self.queues.values()),
            "in_flight": sum(self.labeling.values()),
        }

    async def _hide(self, event: dict, signals: list[str]) -> str:
        decision = authorize("hide_comment_contact_or_link")
        if not decision.allowed:
            log.info("hide %s not allowed (%s); warning instead", event["comment_id"], decision.reason)
            return "warn"
        await self.moderator.hide(event["comment_id"], signals)
        return "hide"

    def _label(self, event, ctx, label, draft, signals, action, infer=None, event_id=None) -> dict:
        partial = {
            "type": "comment_labeled",
            "event_id": event_id or new_id("comment_labeled"),
            "comment_id": event["comment_id"],
            "label": label,
            "order_draft": draft,
            "scam_signals": signals,
            "action": action,
            "latency_ms": max(now_ms() - ctx.received_ms, 0),
        }
        if infer:
            partial["_infer"] = infer
        return partial

    async def _consume(self, sid: str) -> None:
        """Label the session's queue a batch at a time until it is empty."""
        queue, full = self.queues[sid], self.full[sid]
        try:
            while queue:
                wait = queue[0].queued + BATCH_WAIT_S - time.monotonic()
                if len(queue) < BATCH_MAX and wait > 0:
                    full.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(full.wait(), wait)
                    continue
                batch = self._take(sid)
                self.labeling[sid] = len(batch)
                try:
                    await self._label_batch(sid, batch)
                except (
                    Exception
                ) as exc:  # e.g. the post refused: these labels are lost, the queue goes on (C7)
                    log.exception("labels: batch of %d for %s failed: %s", len(batch), sid, exc)
                finally:
                    self.labeling[sid] = 0
        finally:
            del self.consumers[sid]

    def _take(self, sid: str) -> list[Pending]:
        """Cut the next batch now, so a batch holds the comments queued before it and no later ones."""
        queue = self.queues[sid]
        batch = queue[:BATCH_MAX]
        del queue[:BATCH_MAX]
        return batch

    async def _label_batch(self, sid: str, batch: list[Pending]) -> list[dict]:
        if not batch:
            return []
        runner = batch[0].ctx.runner
        versions = await self._versions(sid, runner)
        items, infer = await self._ask(batch, versions)
        events = []
        for p in batch:
            events += await self._consequences(p, items.get(p.event["comment_id"]), versions, infer)
        await runner.post(events)
        return events

    async def _versions(self, sid: str, runner) -> list[dict]:
        """Card versions of the session: those seen on the stream plus, once, the session's history.
        Batches that start while the history loads wait for the same load."""
        if sid not in self.history:
            self.history[sid] = asyncio.ensure_future(self._load_history(sid, runner))
        await asyncio.shield(self.history[sid])
        return self.cards[sid]

    async def _load_history(self, sid: str, runner) -> None:
        try:
            history = []
            params = {"cursor": 0, "follow": "false"}
            async with runner.http.stream("GET", f"/api/sessions/{sid}/events", params=params) as r:
                r.raise_for_status()
                async for _, e in parse_sse(r.aiter_lines()):
                    if e["type"] == "card":
                        history.append(e)
            known = {(c["card_id"], c["version"]) for c in self.cards[sid]}
            self.cards[sid] += [c for c in history if (c["card_id"], c["version"]) not in known]
        except Exception as exc:
            log.warning("labels: card history for %s not loaded (%s)", sid, exc)

    async def _ask(self, batch: list[Pending], versions: list[dict]) -> tuple[dict, tuple | None]:
        comments = []
        for p in batch:
            current, previous = cards_at(versions, p.event["ts_ms"])
            comments.append(
                {
                    "comment_id": p.event["comment_id"],
                    "text": p.event["text"],
                    "current_card": card_brief(current),
                    "previous_card": card_brief(previous),
                }
            )
        context = {
            "catalog": [
                {
                    "sku": c["sku"],
                    "name": c["name"],
                    "sizes": sorted({v["size"] for v in c["variants"] if v["size"]}),
                    "colors": sorted({v["color"] for v in c["variants"] if v["color"]}),
                }
                for c in self.catalog
            ],
            "card_timeline": [card_brief(c) for c in go_live_order(versions)],
            "comments": comments,
        }
        prompt = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]
        try:
            res = await self.client_for(batch[0].ctx).responses(
                prompt, SCHEMA, purpose="label_comments", reasoning=EFFORT
            )
        except Exception as exc:
            log.warning("labels: batch of %d not labeled (%s); labeling them other", len(batch), exc)
            return {}, None
        return {i["comment_id"]: i for i in res.data["items"]}, (res.infer_start_ms, res.infer_end_ms)

    async def _consequences(self, p: Pending, item: dict | None, versions: list[dict], infer) -> list[dict]:
        event, ctx = p.event, p.ctx
        if item is None:
            label, draft, model_signals = "other", None, []
        else:
            label, model_signals = item["label"], [item["contact"]]
            draft = resolve(item, versions, event["ts_ms"], self.skus) if label == "order" else None
        signals = [s for s in SIGNALS if s in set(p.signals) | set(model_signals)]
        action = p.action
        if action == "none" and signals:
            action = await self._hide(event, signals)
        if action == "none" and label == "scam":
            action = "warn"
        label_id = new_id("comment_labeled")
        out = [
            build(
                event,
                self._label(event, ctx, label, draft, signals, action, infer, label_id),
                ctx.received_ms,
            )
        ]
        if draft and draft["reference_kind"] == "unresolved":
            ask = self._ask_again(event, ctx, versions, label_id)
            if ask:
                out.append(build(event, ask, ctx.received_ms))
        return out

    def _ask_again(self, event: dict, ctx: Ctx, versions: list[dict], label_id: str) -> dict | None:
        decision = can_message(event["ts_ms"], now_ms())
        if not decision.allowed:
            log.info("ask_which_item for %s not sent: %s", event["comment_id"], decision.reason)
            return None
        current, previous = cards_at(versions, event["ts_ms"])
        return {
            "type": "message_out",
            "thread_ref": f"th-{event['user_ref']}",
            "template": "ask_which_item",
            "payload": {
                "comment_id": event["comment_id"],
                "options": [card_brief(c) for c in (current, previous) if c],
            },
            "caused_by": [label_id],
        }


LABELER = Labeler()
HANDLERS = LABELER.handlers()
BACKLOGS["labels"] = LABELER.backlog
