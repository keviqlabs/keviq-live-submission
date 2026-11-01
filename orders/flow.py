"""Order flow over one session's events.

    comment_labeled(order) -> DRAFT -> rules -> CONFIRM_SENT + confirm_order   (checks pass, 24h window open)
                                            -> NEEDS_SELLER + waiting_for_shop  (discount, threshold, stock...)
    seller approve / reject (console)       -> CONFIRM_SENT + confirm_order | CANCELLED
    customer yes / no (Messenger)           -> CONFIRMED + receipt           | CANCELLED
    cancel request (comment, plain "huỷ")   -> ask_cancel naming the order(s); CANCEL:<order_id> -> CANCELLED
    several variants, size/color not said   -> ask_variant (no order yet); the customer's pick -> DRAFT -> rules

Functions take a View of the session, a Messenger and `now`, and return partial events (type + fields with
event_id, ts_ms and caused_by) for the caller to post. Every decision comes from orders/rules.py and every state
change is checked by orders/states.py; a refused step raises FlowError and nothing is posted. Event ids are
derived from the trigger, so a replayed trigger produces duplicates the store drops. C6: the DRAFT lists in
caused_by the label, the comment and the card version it was priced from; `order_log()` reads that back.
"""

import hashlib
import re
from dataclasses import dataclass, field

from orders import rules, variants
from orders.intent import cancel_request, customer_reply, discount_request
from orders.states import can_transition, machine
from orders.variants import same, variant_of

LIVE = {"live", "edited"}
HOLDS_STOCK = {"CONFIRM_SENT", "CONFIRMED", "CLOSED"}
TERMINAL = set(machine()["terminal"])
# Comment labels the orders stream reads; scam and spam never reach it. Only order drafts: complaint is read for a
# cancel request alone, since the model may call a plain cancel comment a complaint.
TRIGGER_LABELS = {"order", "other", "complaint"}
MAX_ASK = 12  # Meta allows 13 quick replies; the last one is "Giữ tất cả"
MAX_VARIANT_ASKS = 3  # ask_variant per order, first question included; after that the customer is left alone
PAYMENT_METHOD = "cod"  # what the confirmation states; no code path here takes money (AGENTS.md rule 6)
FIXTURE = re.compile(r"(^|\.)fx-")
UNSAFE = re.compile(r"[^A-Za-z0-9.:-]")


class FlowError(Exception):
    """A step the rules or the state machine refuse; nothing is posted."""


def is_fixture(event_id: str | None) -> bool:
    return bool(FIXTURE.search(event_id or ""))


def safe(s: str) -> str:
    return UNSAFE.sub("-", s)


def order_id_for(comment_id: str) -> str:
    return "o-" + safe(comment_id.removeprefix("cm-"))


def thread_for(customer_ref: str) -> str:
    return "th-" + safe(customer_ref)


def step_id(base: str, order_id: str, step: str) -> str:
    out = f"{base}.{order_id}.{step}"
    return out if len(out) <= 96 else f"h{hashlib.sha1(out.encode()).hexdigest()}.{step}"


def vnd(n: int | None) -> str:
    return "?" if n is None else f"{n:,}".replace(",", ".") + "đ"


def variant_text(v: dict) -> str:
    return ", ".join(x for x in (v.get("size") and f"size {v['size']}", v.get("color")) if x) or "-"


@dataclass
class View:
    cards: list[dict] = field(default_factory=list)  # every card version, arrival order
    comments: dict[str, dict] = field(default_factory=dict)  # comment_id -> {"comment", "labeled"}
    history: dict[str, list[dict]] = field(default_factory=dict)  # order_id -> order events, arrival order
    messages: list[dict] = field(default_factory=list)
    # thread_ref -> customer's last comment/message
    last_contact: dict[str, int] = field(default_factory=dict)

    @classmethod
    def of(cls, events: list[dict]) -> "View":
        v = cls()
        for e in events:
            t = e["type"]
            if t == "card":
                v.cards.append(e)
            elif t in ("comment", "comment_labeled"):
                row = v.comments.setdefault(e["comment_id"], {"comment": None, "labeled": None})
                row["comment" if t == "comment" else "labeled"] = e
                if t == "comment":
                    v.touch(thread_for(e["user_ref"]), e["ts_ms"])
            elif t == "order":
                v.history.setdefault(e["order_id"], []).append(e)
            elif t in ("message_in", "message_out"):
                v.messages.append(e)
                if t == "message_in":
                    v.touch(e["thread_ref"], e["ts_ms"])
        return v

    def touch(self, thread: str, ts: int) -> None:
        self.last_contact[thread] = max(ts, self.last_contact.get(thread, ts))

    def order(self, order_id: str | None) -> dict | None:
        h = self.history.get(order_id or "")
        return h[-1] if h else None

    def card(self, card_id: str, version: int) -> dict | None:
        return next((c for c in self.cards if c["card_id"] == card_id and c["version"] == version), None)

    def open_order(self, thread: str) -> dict | None:
        mine = [h[-1] for h in self.history.values() if thread_for(h[-1]["customer_ref"]) == thread]
        mine = [o for o in mine if o["state"] in ("CONFIRM_SENT", "NEEDS_SELLER")]
        return max(mine, key=lambda o: o["ts_ms"]) if mine else None

    def open_orders(self, thread: str) -> list[dict]:
        """The customer's orders that are not terminal, oldest first; fixture orders are left out."""
        mine = [
            h[-1]
            for h in self.history.values()
            if thread_for(h[-1]["customer_ref"]) == thread
            and h[-1]["state"] not in TERMINAL
            and not is_fixture(h[-1]["event_id"])
        ]
        return sorted(mine, key=lambda o: (self.history[o["order_id"]][0]["ts_ms"], o["order_id"]))


def card_at(cards: list[dict], card_id: str, ts: int) -> dict | None:
    """The version of card_id that was current at ts (latest version at or before ts), whatever its status."""
    best = None
    for c in cards:
        if c["card_id"] == card_id and c["ts_ms"] <= ts:
            if best is None or (c["ts_ms"], c["version"]) >= (best["ts_ms"], best["version"]):
                best = c
    return best


def held(view: View, card_id: str, variant: dict, except_order: str) -> int:
    """Units of this card variant already promised to other orders (confirmation sent or confirmed)."""
    n = 0
    for oid, h in view.history.items():
        if oid == except_order or h[-1]["state"] not in HOLDS_STOCK:
            continue
        for line in h[-1]["lines"]:
            v = line["variant"]
            if (
                line["card_id"] == card_id
                and same(v["size"], variant["size"])
                and same(v["color"], variant["color"])
            ):
                n += line["qty"]
    return n


def stamp(partial: dict, event_id: str, ts_ms: int, caused_by: list[str]) -> dict:
    return {**partial, "event_id": event_id, "ts_ms": ts_ms, "caused_by": caused_by}


def new_order(order_id: str, customer_ref: str, lines: list[dict], reason: str) -> dict:
    ok, why = can_transition(None, "DRAFT", "assistant")
    if not ok:
        raise FlowError(f"{order_id}: {why}")
    return {
        "type": "order",
        "order_id": order_id,
        "customer_ref": customer_ref,
        "lines": lines,
        "amount": sum(x["unit_price"] * x["qty"] for x in lines),
        "state": "DRAFT",
        "prev_state": None,
        "actor": "assistant",
        "approval_needed": False,
        "reason": reason,
        "receipt": None,
    }


def transition(
    prev: dict, state: str, actor: str, reason: str, *, lines=None, receipt=None, confirmed=False
) -> dict:
    ok, why = can_transition(prev["state"], state, actor, confirmed)
    if not ok:
        raise FlowError(f"{prev['order_id']}: {why}")
    lines = prev["lines"] if lines is None else lines
    return {
        "type": "order",
        "order_id": prev["order_id"],
        "customer_ref": prev["customer_ref"],
        "lines": lines,
        "amount": sum(x["unit_price"] * x["qty"] for x in lines),
        "state": state,
        "prev_state": prev["state"],
        "actor": actor,
        "approval_needed": state == "NEEDS_SELLER",
        "reason": reason,
        "receipt": prev.get("receipt") if receipt is None else receipt,
    }


def confirm_payload(order: dict, card: dict | None, comment_id: str | None) -> dict:
    line = order["lines"][0]
    name = (card or {}).get("name") or line["sku"] or line["card_id"]
    card_price = (card or {}).get("price")
    price_note = "" if line["unit_price"] == card_price else f" (giá thẻ {vnd(card_price)}, shop đã duyệt)"
    text = (
        f"Shop xác nhận đơn: {name}, {variant_text(line['variant'])}, SL {line['qty']} x {vnd(line['unit_price'])}"
        f"{price_note} = {vnd(order['amount'])}. Thanh toán khi nhận hàng (COD). "
        "Bạn gửi địa chỉ nhận hàng và bấm Đồng ý để chốt đơn nhé."
    )
    payload = {
        "order_id": order["order_id"],
        "card_id": line["card_id"],
        "card_version": line["card_version"],
        "sku": line["sku"],
        "name": name,
        "size": line["variant"]["size"],
        "color": line["variant"]["color"],
        "qty": line["qty"],
        "unit_price": line["unit_price"],
        "card_price": card_price,
        "amount": order["amount"],
        "address": None,
        "payment_method": PAYMENT_METHOD,
        "text": text,
        "quick_replies": [["Đồng ý", f"CONFIRM:{order['order_id']}"], ["Huỷ", f"CANCEL:{order['order_id']}"]],
    }
    if comment_id:
        payload["comment_id"] = comment_id  # first reply to a commenter is a private reply to the comment
    return payload


def waiting_payload(order: dict, card: dict | None, comment_id: str | None) -> dict:
    line = order["lines"][0]
    name = (card or {}).get("name") or line["sku"] or line["card_id"]
    payload = {
        "order_id": order["order_id"],
        "text": f"Shop đã nhận đơn {name}, {variant_text(line['variant'])}, SL {line['qty']}. "
        "Shop kiểm tra rồi nhắn lại bạn nhé.",
    }
    if comment_id:
        payload["comment_id"] = comment_id
    return payload


def order_text(view: View, order: dict) -> str:
    line = order["lines"][0]
    card = view.card(line["card_id"], line["card_version"])
    name = (card or {}).get("name") or line["sku"] or line["card_id"]
    return f"{order['order_id']} {name}, {variant_text(line['variant'])}, SL {line['qty']} = {vnd(order['amount'])}"


async def ask_cancel(
    trigger: dict,
    view: View,
    messenger,
    now: int,
    thread: str,
    sources: list[str] = (),
    comment_id: str | None = None,
) -> list[dict]:
    """Ask the customer which of their open orders to cancel. Never picks one and changes no state: only the
    customer's CANCEL:<order_id> quick reply cancels. No open order or no 24h window -> nothing is sent. A comment
    is asked once, however many labels it gets (the shield's interim label, then the batch label)."""
    mine = view.open_orders(thread)
    if not mine or not rules.can_message(view.last_contact.get(thread), now).allowed:
        return []
    if comment_id and any(
        m["type"] == "message_out"
        and m.get("template") == "ask_cancel"
        and (m.get("payload") or {}).get("comment_id") == comment_id
        for m in view.messages
    ):
        return []
    if len(mine) == 1:
        oid = mine[0]["order_id"]
        payload = {
            "order_id": oid,
            "text": f"Bạn muốn huỷ đơn {order_text(view, mine[0])}? Bấm Huỷ đơn để huỷ, Giữ đơn để giữ lại.",
            "quick_replies": [["Huỷ đơn", f"CANCEL:{oid}"], ["Giữ đơn", f"KEEP:{oid}"]],
        }
    else:
        shown = mine[:MAX_ASK]
        listed = "; ".join(f"{i}) {order_text(view, o)}" for i, o in enumerate(shown, 1))
        payload = {
            "order_ids": [o["order_id"] for o in shown],
            "text": f"Bạn đang có {len(mine)} đơn: {listed}. Bạn muốn huỷ đơn nào?",
            "quick_replies": [[f"Huỷ đơn {i}", f"CANCEL:{o['order_id']}"] for i, o in enumerate(shown, 1)]
            + [["Giữ tất cả", "KEEP:all"]],
        }
    if comment_id:
        payload["comment_id"] = comment_id
    event_id = step_id(trigger["event_id"], mine[0]["order_id"] if len(mine) == 1 else "orders", "ask-cancel")
    if any(m["event_id"] == event_id for m in view.messages):
        return []
    msg = await messenger.send(thread, "ask_cancel", payload)
    return [stamp(msg, event_id, now, [trigger["event_id"], *sources, *(o["event_id"] for o in mine)])]


async def on_labeled(trigger: dict, view: View, messenger, now: int) -> list[dict]:
    """comment_labeled(order) -> DRAFT priced from the card version current at the comment, then the rules.
    A cancel request in the comment (label order, other or complaint) never drafts: it may only lead to
    ask_cancel. Negated or unclear cancel words ("chốt 1 đôi, ko huỷ nha") are not a cancel: label order drafts,
    label other or complaint sends nothing."""
    comment = (view.comments.get(trigger["comment_id"]) or {}).get("comment")
    if trigger.get("label") not in TRIGGER_LABELS or is_fixture(trigger["event_id"]) or comment is None:
        return []
    cancel = cancel_request(comment.get("text"))
    if cancel and cancel["kind"] == "cancel":
        thread = thread_for(comment["user_ref"])
        return await ask_cancel(
            trigger, view, messenger, now, thread, [comment["event_id"]], trigger["comment_id"]
        )
    draft_in = trigger.get("order_draft") or {}
    card_id = draft_in.get("referenced_card_id")
    if trigger.get("label") != "order" or not card_id:
        return []  # unresolved references are the comments stream's question to the customer
    order_id = order_id_for(trigger["comment_id"])
    if view.order(order_id):
        return []
    blocking, notes = [], []
    card = card_at(view.cards, card_id, comment["ts_ms"])
    if card is None:
        card = card_at(view.cards, card_id, now)
        if card is None:
            return []
        blocking.append(f"{card_id} had no version when the comment was written")
    elif card["status"] not in LIVE:
        blocking.append(f"{card_id} v{card['version']} was {card['status']} when the comment was written")
    qty, notes = draft_qty(draft_in, notes)
    want = variants.want_of(draft_in)
    variant = dict(want)
    hits = variants.hit_indexes(card, want)
    if len(hits) == 1:
        variant, stock = variant_of(card["variants"][hits[0]]), card["variants"][hits[0]].get("stock") or 0
    elif not card.get("variants"):
        stock = card.get("stock") or 0
    else:
        missing = variants.missing_fields(card, hits, want)
        if missing and not blocking:
            causes = [trigger["event_id"], comment["event_id"], card["event_id"]]
            asked = await ask_variant(
                trigger["event_id"], view, messenger, now, comment, card, order_id, hits, missing, causes
            )
            if asked is not None:
                return asked
        stock = 0
        how = "not on" if not hits else "ambiguous on"
        blocking.append(f"variant {variant_text(variant)} {how} {card_id} v{card['version']}")
    return await draft_order(
        trigger["event_id"],
        view,
        messenger,
        now,
        comment,
        card,
        order_id,
        variant,
        stock,
        qty,
        notes,
        blocking,
        [comment["event_id"], card["event_id"]],
        reply_to=trigger["comment_id"],
    )


def draft_qty(draft_in: dict, notes: list[str]) -> tuple[int, list[str]]:
    qty = draft_in.get("qty")
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
        return 1, [*notes, "qty not given, drafted 1"]
    return qty, notes


async def draft_order(
    base: str,
    view: View,
    messenger,
    now: int,
    comment: dict,
    card: dict,
    order_id: str,
    variant: dict,
    stock: int,
    qty: int,
    notes: list[str],
    blocking: list[str],
    causes: list[str],
    reply_to: str | None,
) -> list[dict]:
    """DRAFT priced from `card` (the version current at the comment), then the rules: CONFIRM_SENT +
    confirm_order, or NEEDS_SELLER + waiting_for_shop. `reply_to` is the comment for a private reply; None
    sends by PSID (Meta allows one private reply per comment)."""
    card_id = card["card_id"]
    hold = held(view, card_id, variant, order_id)
    if hold:
        notes = [*notes, f"{hold} already held by other orders"]
    price = card.get("price")
    line = {
        "sku": card.get("sku"),
        "card_id": card_id,
        "card_version": card["version"],
        "variant": variant,
        "qty": qty,
        "unit_price": price if price is not None else 0,
    }
    customer_ref = comment["user_ref"]
    thread = thread_for(customer_ref)
    discount = discount_request(comment.get("text"))
    decision = rules.evaluate_draft(
        price, price, qty, max(stock - hold, 0), discount_requested=bool(discount)
    )
    window = rules.can_message(view.last_contact.get(thread, comment["ts_ms"]), now)

    source = f"comment {comment['comment_id']} on {card_id} v{card['version']} ({vnd(price)})"
    draft = new_order(order_id, customer_ref, [line], "; ".join([f"drafted from {source}", *notes]))
    out = [stamp(draft, step_id(base, order_id, "draft"), now, causes)]
    sources = [out[0]["event_id"], comment["event_id"], card["event_id"]]
    if decision.allowed and not blocking and window.allowed:
        nxt = transition(draft, "CONFIRM_SENT", "assistant", f"draft checks passed: {decision.reason}")
        msg = await messenger.send(thread, "confirm_order", confirm_payload(nxt, card, reply_to))
    else:
        why = blocking or [decision.reason]
        if discount:
            why[-1] += f' ("{discount["phrase"]}")'
        if not window.allowed:
            why.append(f"cannot message the customer: {window.reason}")
        nxt = transition(draft, "NEEDS_SELLER", "assistant", "; ".join(why))
        msg = None
        if window.allowed:
            msg = await messenger.send(thread, "waiting_for_shop", waiting_payload(nxt, card, reply_to))
    out.append(stamp(nxt, step_id(base, order_id, "decide"), now + 1, sources))
    if msg:
        out.append(stamp(msg, step_id(base, order_id, "msg"), now + 2, [out[1]["event_id"]]))
    return out


async def ask_variant(
    base: str,
    view: View,
    messenger,
    now: int,
    comment: dict,
    card: dict,
    order_id: str,
    hits: list[int],
    missing: list[str],
    causes: list[str],
    again: bool = False,
) -> list[dict] | None:
    """Ask the commenter to choose the missing size/color among the variants of `card` (the version the comment
    was priced from) that still have stock. Nothing is guessed and no order event is made until the customer
    answers. None when it cannot ask (no 24h window, no variant left): the caller falls back to the seller."""
    thread = thread_for(comment["user_ref"])
    window = rules.can_message(view.last_contact.get(thread, comment["ts_ms"]), now)
    vs = card["variants"]
    free = [
        i for i in hits if (vs[i].get("stock") or 0) - held(view, card["card_id"], variant_of(vs[i]), "") > 0
    ]
    if not window.allowed or not free:
        return None
    event_id = step_id(base, order_id, "ask-variant")
    if any(m["event_id"] == event_id for m in view.messages):
        return []
    payload = variants.question(
        card, order_id, comment["comment_id"], free, missing, again=again, price=vnd(card.get("price"))
    )
    if not again:
        payload["comment_id"] = comment["comment_id"]  # the first reply to a commenter is a private reply
    msg = await messenger.send(thread, "ask_variant", payload)
    return [stamp(msg, event_id, now, causes)]


def pending_asks(view: View, thread: str) -> dict[str, list[dict]]:
    """order_id -> ask_variant messages sent to this thread for an order that was not drafted yet."""
    out: dict[str, list[dict]] = {}
    for m in view.messages:
        oid = (m.get("payload") or {}).get("order_id")
        if (
            m["type"] == "message_out"
            and m.get("template") == "ask_variant"
            and m["thread_ref"] == thread
            and oid
            and view.order(oid) is None
        ):
            out.setdefault(oid, []).append(m)
    return out


async def on_variant_reply(trigger: dict, view: View, messenger, now: int, kind, order_id) -> list[dict]:
    """The customer's answer to ask_variant: a PICK:<order_id>#<i> quick reply, or text naming the missing
    size/color ("39", "size M"). Exactly one variant of the asked card version -> the usual draft (card price of
    that version, stock, rules, 24h window), sent by PSID. Unclear, not on the card or a bogus index -> ask again,
    MAX_VARIANT_ASKS in all; no order is made. Only the thread that was asked can answer; with two open
    questions in the thread, text never picks one."""
    thread = trigger["thread_ref"]
    pending = pending_asks(view, thread)
    payload = trigger.get("payload") or {}
    if order_id is None and pending:
        order_id = max(pending, key=lambda o: (pending[o][-1]["ts_ms"], o))
    asks = pending.get(order_id or "")
    if not asks:
        return []  # nothing asked in this thread, another customer's order, or already drafted
    cancel = cancel_request(payload.get("text"))
    if kind is None and cancel and cancel["kind"] == "cancel":
        return []  # "thôi không lấy nữa": no order exists, nothing to ask again
    ask = asks[-1]
    q = ask["payload"]
    card = view.card(q["card_id"], q["card_version"])
    row = view.comments.get(q["source_comment_id"]) or {}
    comment, labeled = row.get("comment"), row.get("labeled")
    if card is None or comment is None or labeled is None:
        return []
    draft_in = labeled.get("order_draft") or {}
    want = variants.want_of(draft_in)
    hits = variants.hit_indexes(card, want)
    missing = variants.missing_fields(card, hits, want)
    if kind == "pick":
        i = variants.picked(payload, hits)
        choice = [] if i is None else [i]
    elif len(pending) > 1:
        choice = []
    else:
        choice = variants.named(payload.get("text"), card, hits, missing)
    tid = trigger["event_id"]
    if len(choice) == 1:
        v = card["variants"][choice[0]]
        qty, notes = draft_qty(draft_in, [])
        notes = [*notes, f"{variants.choice_text(v, missing)} chosen by the customer in Messenger"]
        causes = [comment["event_id"], card["event_id"], ask["event_id"], tid]
        return await draft_order(
            tid,
            view,
            messenger,
            now,
            comment,
            card,
            order_id,
            variant_of(v),
            v.get("stock") or 0,
            qty,
            notes,
            [],
            causes,
            reply_to=None,
        )
    if len(asks) >= MAX_VARIANT_ASKS:
        return []
    again = await ask_variant(
        tid, view, messenger, now, comment, card, order_id, hits, missing, [tid, ask["event_id"]], again=True
    )
    return again or []


def receipt_payload(order: dict, card: dict | None, receipt: dict, address: str | None) -> dict:
    line = order["lines"][0]
    name = (card or {}).get("name") or line["sku"] or line["card_id"]
    subtitle = f"{variant_text(line['variant'])} · {line['qty']} x {vnd(line['unit_price'])} = {vnd(order['amount'])}"
    return {
        "order_id": order["order_id"],
        "card_id": receipt.get("card_id"),
        "card_version": receipt["card_version"],
        "image_url": receipt["image_url"],
        "card_price": receipt["price"],
        "unit_price": line["unit_price"],
        "qty": line["qty"],
        "amount": order["amount"],
        "address": address,
        "payment_method": PAYMENT_METHOD,
        "text": f"Đơn {order['order_id']} đã chốt: {name}, {subtitle}. Thanh toán khi nhận hàng (COD).",
        "elements": [{"title": name, "image_url": receipt["image_url"], "subtitle": subtitle}],
    }


def offers(card: dict, variant: dict) -> bool:
    """The card lists the line's variant; a card without variants fits only a line without size and color."""
    if not card.get("variants"):
        return variant["size"] is None and variant["color"] is None
    return bool(variants.hit_indexes(card, variant))


def card_change(view: View, order: dict, at: int) -> tuple[dict | None, str | None]:
    """The card version current at `at` and, when it differs in price from the version current when the
    confirmation was sent (CONFIRM_SENT ts_ms, a seller approval included) or no longer lists the line's variant,
    why the order goes back to the seller. A change between the comment and the send is not one: the order keeps
    the comment-time price it was sent with."""
    line = order["lines"][0]
    card_id = line["card_id"]
    seen = card_at(view.cards, card_id, order["ts_ms"]) or view.card(card_id, line["card_version"])
    latest = card_at(view.cards, card_id, at)
    if seen is None or latest is None or latest["version"] == seen["version"]:
        return latest, None
    changed = f"card {card_id} changed before the customer's ok"
    old, new = f"v{seen['version']}", f"v{latest['version']}"
    if latest["price"] != seen["price"]:
        return latest, f"{changed}: {old} {vnd(seen['price'])} -> {new} {vnd(latest['price'])}"
    if offers(seen, line["variant"]) and not offers(latest, line["variant"]):
        return latest, f"{changed}: {new} no longer lists {variant_text(line['variant'])} ({old} did)"
    return latest, None


def answers_cancel_question(view: View, trigger: dict) -> bool:
    """Before the trigger in its thread, the shop's last message is the ask_cancel question or the customer's last
    plain "huỷ" / "không" came after it (that gets ask_cancel too), and no reply naming an order (a CONFIRM, CANCEL,
    KEEP or PICK button) came after either. Other plain text in between ("sao vậy shop", or a first "có" whose
    question asked again is stored after this one) leaves the question open."""
    open_question = False
    for m in view.messages:
        if m["event_id"] == trigger["event_id"]:
            break
        if m["thread_ref"] != trigger["thread_ref"]:
            continue
        if m["type"] == "message_out":
            open_question = m.get("template") == "ask_cancel"
            continue
        kind, order_id = customer_reply(m.get("template"), m.get("payload") or {})
        if order_id:
            open_question = False
        elif kind == "no":
            open_question = True
    return open_question


async def on_reply(trigger: dict, view: View, messenger, now: int) -> list[dict]:
    """The customer's yes / no / keep / variant pick in Messenger. Only the customer's own thread can confirm or
    cancel its order, and only a reply naming the order (CANCEL:<order_id>) cancels; a plain "huỷ" gets
    ask_cancel, and so does a plain yes to a cancel question no button has answered (answers_cancel_question).
    Other text may answer an open ask_variant question (on_variant_reply). A yes after the card's price changed,
    or after its new version dropped the line's variant, goes to the seller (card_change)."""
    payload = trigger.get("payload") or {}
    kind, order_id = customer_reply(trigger.get("template"), payload)
    thread = trigger["thread_ref"]
    if kind == "keep" or is_fixture(trigger["event_id"]):
        return []
    if kind == "yes" and not order_id and answers_cancel_question(view, trigger):
        # "có" to "huỷ đơn ...?" is not an ok to the order; the CONFIRM:<order_id> button still confirms
        return await ask_cancel(trigger, view, messenger, now, thread)
    if kind == "yes" and not order_id and view.open_order(thread) is None and pending_asks(view, thread):
        kind = None  # "ok" to "which size?" names no size: it is asked again
    if kind in (None, "pick"):
        return await on_variant_reply(trigger, view, messenger, now, kind, order_id)
    if kind == "no" and not order_id:
        return await ask_cancel(trigger, view, messenger, now, thread)
    order = view.order(order_id) if order_id else view.open_order(thread)
    if order is None or is_fixture(order["event_id"]) or thread_for(order["customer_ref"]) != thread:
        return []
    order_id, tid = order["order_id"], trigger["event_id"]
    causes = [order["event_id"]]
    if kind == "no":
        if order["state"] in TERMINAL:
            return []  # a repeated CANCEL: no second transition, no second message
        ev = transition(order, "CANCELLED", "customer", f"customer chose CANCEL:{order_id} in Messenger")
        msg = await messenger.send(
            thread, "order_cancelled", {"order_id": order_id, "text": "Shop đã huỷ đơn theo yêu cầu."}
        )
    elif order["state"] == "CONFIRM_SENT":
        line = order["lines"][0]
        card = view.card(line["card_id"], line["card_version"])
        # A change known when the ok is handled counts, even if written just after the customer's ok.
        latest, changed = card_change(view, order, max(trigger["ts_ms"], now))
        moved = latest is not None and latest["version"] != line["card_version"]
        if moved:
            causes.append(latest["event_id"])
        if changed:
            ev = transition(order, "NEEDS_SELLER", "assistant", f"{changed}; the customer confirms again")
            msg = await messenger.send(thread, "waiting_for_shop", waiting_payload(ev, card, None))
        else:
            price = card["price"] if card and card["price"] is not None else line["unit_price"]
            receipt = {
                "card_id": line["card_id"],
                "card_version": line["card_version"],
                "image_url": (card or {}).get("image_url"),
                "price": price,
            }
            reason = "customer confirmed in Messenger"
            if moved:
                reason += f" (order on card v{line['card_version']}, v{latest['version']} at the ok)"
            ev = transition(order, "CONFIRMED", "customer", reason, receipt=receipt)
            address = payload.get("address")
            msg = await messenger.send(thread, "receipt", receipt_payload(ev, card, receipt, address))
    elif order["state"] == "NEEDS_SELLER":
        card = view.card(order["lines"][0]["card_id"], order["lines"][0]["card_version"])
        msg = await messenger.send(thread, "waiting_for_shop", waiting_payload(order, card, None))
        return [stamp(msg, step_id(tid, order_id, "msg"), now, [order["event_id"]])]
    else:
        return []
    first = stamp(ev, step_id(tid, order_id, "reply"), now, causes)
    return [first, stamp(msg, step_id(tid, order_id, "msg"), now + 1, [first["event_id"]])]


async def decide(
    view: View, order_id: str, decision: str, messenger, now: int, unit_price=None
) -> list[dict]:
    """The seller's one-tap decision on an order in NEEDS_SELLER. Returns partials caused by the order event."""
    order = view.order(order_id)
    if order is None:
        raise LookupError(order_id)
    if order["state"] != "NEEDS_SELLER":
        raise FlowError(f"{order_id} is {order['state']}, not waiting for the seller")
    thread = thread_for(order["customer_ref"])
    line = order["lines"][0]
    card = view.card(line["card_id"], line["card_version"])
    window = rules.can_message(view.last_contact.get(thread), now)
    base = order["event_id"]
    if decision == "reject":
        ev = transition(order, "CANCELLED", "seller", f"seller rejected (was: {order['reason']})")
        msg = None
        if window.allowed:
            text = "Shop xin lỗi, đơn này shop chưa nhận được. Bạn xem món khác trên live nhé."
            msg = await messenger.send(thread, "order_cancelled", {"order_id": order_id, "text": text})
    elif decision == "approve":
        card_price = (card or {}).get("price")
        if unit_price is None:
            if card_price is None:
                raise FlowError(f"{order_id}: card has no price, the seller must set a unit price")
            unit_price = card_price
        change = rules.price_change(unit_price, card_price, "seller")
        if not change.allowed:
            raise FlowError(f"{order_id}: {change.reason}")
        if not window.allowed:
            raise FlowError(f"{order_id}: cannot message the customer: {window.reason}")
        lines = [{**x, "unit_price": unit_price} for x in order["lines"]]
        ev = transition(
            order,
            "CONFIRM_SENT",
            "seller",
            f"seller approved: {change.reason} (was: {order['reason']})",
            lines=lines,
        )
        msg = await messenger.send(thread, "confirm_order", confirm_payload(ev, card, None))
    else:
        raise FlowError(f"unknown decision {decision!r}")
    first = stamp(ev, step_id(base, order_id, decision), now, [base])
    out = [first]
    if msg:
        out.append(stamp(msg, step_id(base, order_id, f"{decision}-msg"), now + 1, [first["event_id"]]))
    return out


def order_log(view: View, order_id: str) -> dict | None:
    """C6: where each step of an order came from — comment, label, card version, decisions, messages."""
    history = view.history.get(order_id)
    if not history:
        return None
    first = history[0]
    row = next(
        (
            r
            for r in view.comments.values()
            if r["comment"] and r["comment"]["event_id"] in first.get("caused_by", [])
        ),
        {},
    )
    comment, labeled = row.get("comment"), row.get("labeled")
    line = first["lines"][0]
    card = view.card(line["card_id"], line["card_version"])
    thread = thread_for(first["customer_ref"])
    ids = {e["event_id"] for e in history}
    messages = [
        m
        for m in view.messages
        if (m["type"] == "message_out" and (m.get("payload") or {}).get("order_id") == order_id)
        or (m["type"] == "message_in" and m["thread_ref"] == thread and m["ts_ms"] >= first["ts_ms"])
        or ids & set(m.get("caused_by") or [])
        or m["event_id"] in first.get("caused_by", [])
    ]
    pick = (
        "event_id",
        "ts_ms",
        "state",
        "prev_state",
        "actor",
        "reason",
        "amount",
        "lines",
        "receipt",
        "timing",
    )
    return {
        "order_id": order_id,
        "steps": [{k: e.get(k) for k in pick} for e in history],
        "comment": comment
        and {k: comment.get(k) for k in ("event_id", "comment_id", "user_ref", "text", "ts_ms")},
        "label": labeled
        and {k: labeled.get(k) for k in ("event_id", "label", "order_draft", "latency_ms", "ts_ms")},
        "card": card
        and {
            k: card.get(k)
            for k in ("event_id", "card_id", "version", "name", "price", "image_url", "status", "ts_ms")
        },
        "discount_request": discount_request((comment or {}).get("text")),
        "messages": [
            {k: m.get(k) for k in ("event_id", "type", "ts_ms", "template")}
            | {"text": (m.get("payload") or {}).get("text")}
            for m in messages
        ],
    }
