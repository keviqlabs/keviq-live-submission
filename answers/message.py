"""A private reply to a viewer's price or stock question about the item on screen.

on_question() turns one price_question or stock_question label into at most one message_out that carries the
question's comment_id; the first reply to a commenter goes by comment_id (a private reply). The item is the card
on screen when the comment was written (app/current.py) and its facts come from that card's latest version, read
only. The sentence comes from answers/reply.py: an approved price, an approved stock count, or "the seller will
confirm" when a fact is missing; nothing is guessed. Right before the message is built the item must still be
on screen (the latest appearance stored) and check() re-reads the sentence against the card's latest version found
again in the events; any mismatch drops it.

Silent cases: no card on screen or a retired one, a question in the first GUARD_MS after the item changed (it may
be about the item before), a size question about an order (the order flow asks for a size, not this module), the
24 h window closed, or a reply already posted for that comment. No order event, no price change, no discount.
"""

import hashlib
import re

from app.current import appearances, current_at
from orders.flow import View, is_fixture, thread_for
from orders.rules import can_message

from .facts import facts_from_card
from .questions import parse
from .reply import check, compose

QUESTION_LABELS = ("price_question", "stock_question")
TEMPLATE = "answer_question"
GUARD_MS = 3000
SPOKEN = ("answer", "defer")
_UNSAFE = re.compile(r"[^A-Za-z0-9._:-]")


def answer_id(comment_id: str) -> str:
    """Event id of the reply, derived from the comment, so a replayed label makes a duplicate the store drops."""
    out = "ans." + _UNSAFE.sub("-", comment_id)
    return out if len(out) <= 96 else "ans.h" + hashlib.sha1(comment_id.encode()).hexdigest()


def switched_at(cards: list[dict], card_id: str, ts_ms: int) -> int | None:
    """Frame time at which card_id became the item on screen, for the stretch that is on screen at ts_ms."""
    start, shown = None, None
    for card in appearances(cards):
        frame = card["provenance"]["frame_ts_ms"]
        if frame > ts_ms:
            break
        if card["card_id"] != shown:
            start, shown = frame, card["card_id"]
    return start if shown == card_id else None


def latest_card(events: list[dict], card_id: str) -> dict | None:
    return max(
        (e for e in events if e["type"] == "card" and e["card_id"] == card_id),
        key=lambda e: e["version"],
        default=None,
    )


def answered(events: list[dict], comment_id: str) -> bool:
    return any(
        e["type"] == "message_out"
        and e.get("template") == TEMPLATE
        and (e.get("payload") or {}).get("comment_id") == comment_id
        for e in events
    )


async def on_question(label: dict, events: list[dict], messenger, now: int) -> tuple[list[dict], str]:
    """(partials, reason): one message_out partial with "answer" or "defer", or no partial and why."""
    if label.get("label") not in QUESTION_LABELS or is_fixture(label.get("event_id")):
        return [], "not_a_question"
    cid = label["comment_id"]
    comment = next((e for e in events if e["type"] == "comment" and e["comment_id"] == cid), None)
    if comment is None:
        return [], "no_comment"
    if answered(events, cid):
        return [], "already_answered"
    q = parse(comment.get("text") or "", label["label"])
    if q is None:
        return [], "not_answered_here"
    ts = comment["ts_ms"]
    cards = [e for e in events if e["type"] == "card"]
    current, _ = current_at(cards, ts)
    if current is None or current.get("status") == "retired":
        return [], "no_card"
    since = switched_at(cards, current["card_id"], ts)
    if since is not None and ts - since < GUARD_MS:
        return [], "just_switched"
    answer = compose(q, facts_from_card(current))
    if answer.action not in SPOKEN:
        return [], answer.reason
    screen, _ = current_at(cards, max(now, ts))
    if screen is None or screen["card_id"] != current["card_id"]:
        return [], "item_changed"
    again = latest_card(events, current["card_id"])
    if again is None or again.get("status") == "retired":
        return [], "no_card"
    reason = check(answer, facts_from_card(again))
    if reason is not None:
        return [], reason
    thread = thread_for(comment["user_ref"])
    if not can_message(View.of(events).last_contact.get(thread, ts), now).allowed:
        return [], "no_window"
    payload = {
        "comment_id": cid,
        "card_id": answer.card_id,
        "card_version": answer.version,
        "kind": answer.kind,
        "text": answer.text,
    }
    msg = await messenger.send(thread, TEMPLATE, payload)
    causes = [label["event_id"], comment["event_id"], again["event_id"]]
    return [{**msg, "event_id": answer_id(cid), "ts_ms": now, "caused_by": causes}], answer.action
