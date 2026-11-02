"""Transactional order handoff from immutable attachment decisions."""

import copy
import hashlib

from fastapi import HTTPException

from answers.message import QUESTION_LABELS, on_question
from app.contracts import make_event
from connectors.sim.messenger import SimMessenger
from orders import flow, rules, variants


def order_id_for(comment_id):
    """Preserve the exact source identity; a separate bounded namespace handles long IDs."""
    value = "o-" + comment_id
    return value if len(value) <= 96 else "oh-" + hashlib.sha256(comment_id.encode()).hexdigest()[:48]


def qualified(card):
    return (
        card is not None
        and card["sku"] is not None
        and card["price"] is not None
        and card["status"] in flow.LIVE
    )


def latest_card(events, card_id):
    return max(
        (e for e in events if e["type"] == "card" and e["card_id"] == card_id),
        key=lambda e: e["version"],
        default=None,
    )


def build_partials(sid, source, partials):
    out = []
    for partial in partials:
        fields = dict(partial)
        event = make_event(fields.pop("type"), sid, fields.pop("ts_ms"), source, **fields)
        out.append(event)
    return out


async def append_partials(store, sid, source, partials, *, intent=None):
    from app.store import VersionTaken

    out = build_partials(sid, source, partials)
    try:
        await store.append_order_batch(out, intent=intent)
    except VersionTaken as exc:
        raise HTTPException(409, "canonical event identity is already occupied") from exc
    return out


async def reply_partials(store, sid, trigger, view, events, now):
    """A variant answer preserves the asked identity and revalidates its current approved version."""
    kind, oid = flow.customer_reply(trigger.get("template"), trigger.get("payload") or {})
    pending = flow.pending_asks(view, trigger["thread_ref"])
    target = view.order(oid) if oid else view.open_order(trigger["thread_ref"])
    if target and view.history[target["order_id"]][0].get("seq", float("inf")) >= trigger["seq"]:
        return []  # An input received before this order cannot become a later customer confirmation.
    if kind == "yes" and target and target["state"] == "CONFIRM_SENT":
        sent = next(
            (
                message
                for message in reversed(view.messages)
                if message["type"] == "message_out"
                and message["template"] == "confirm_order"
                and (message.get("payload") or {}).get("order_id") == target["order_id"]
                and target["event_id"] in message.get("caused_by", [])
            ),
            None,
        )
        if (
            sent is None
            or trigger["seq"] <= sent.get("seq", float("inf"))
            or trigger["ts_ms"] < sent["ts_ms"]
        ):
            return []  # Late delivery of an older yes cannot approve a newer confirmation.
    if kind == "yes" and not oid and view.open_order(trigger["thread_ref"]) is None and pending:
        kind = None
    if kind not in (None, "pick"):
        return await flow.on_reply(trigger, view, SimMessenger(), now)
    if not oid and len(pending) == 1:
        oid = next(iter(pending))
    asks = pending.get(oid or "")
    if not asks:
        return await flow.on_reply(trigger, view, SimMessenger(), now)
    ask, payload = asks[-1], trigger.get("payload") or {}
    if ask.get("seq", float("inf")) >= trigger["seq"]:
        return []
    question = ask["payload"]
    attachment = next(
        (
            e
            for e in events
            if e["type"] == "comment_attachment" and e["comment_id"] == question["source_comment_id"]
        ),
        None,
    )
    if attachment is None:
        return await flow.on_reply(trigger, view, SimMessenger(), now)
    if attachment["action"] == "reject" or attachment["selected_card_id"] != question["card_id"]:
        return []
    card = latest_card(events, attachment["selected_card_id"])
    if not qualified(card):
        return []
    original = view.card(question["card_id"], question["card_version"])
    row = view.comments.get(question["source_comment_id"]) or {}
    comment, label = row.get("comment"), row.get("labeled")
    if original is None or comment is None or label is None:
        return []
    cancellation = flow.cancel_request(payload.get("text"))
    if cancellation and cancellation["kind"] == "cancel":
        return []
    draft = label.get("order_draft") or {}
    want = variants.want_of(draft)
    hits = variants.hit_indexes(original, want)
    missing = variants.missing_fields(original, hits, want)
    if kind == "pick":
        picked = variants.picked(payload, hits)
        choice = [] if picked is None else [picked]
    elif len(pending) == 1:
        choice = variants.named(payload.get("text"), original, hits, missing)
    else:
        choice = []
    if len(choice) != 1:
        return await flow.on_reply(trigger, view, SimMessenger(), now)
    selected = variants.variant_of(original["variants"][choice[0]])
    current_hits = variants.hit_indexes(card, selected)
    if len(current_hits) != 1:
        return []  # The asked variant no longer exists; never reinterpret its old index as another variant.
    variant = card["variants"][current_hits[0]]
    qty, notes = flow.draft_qty(draft, ["variant chosen by the customer on the selected attachment card"])
    partials = await flow.draft_order(
        trigger["event_id"],
        view,
        SimMessenger(),
        now,
        comment,
        card,
        oid,
        variants.variant_of(variant),
        variant.get("stock") or 0,
        qty,
        notes,
        [],
        [
            attachment["event_id"],
            comment["event_id"],
            label["event_id"],
            card["event_id"],
            ask["event_id"],
            trigger["event_id"],
        ],
        reply_to=None,
    )
    await store.reserve_attachment_order(sid, comment["comment_id"], oid)
    return partials


async def draft_attachment(
    store, attachment, events, comment, label, card, now, candidates=None, *, persist=True
):
    """No placeholder price: eligibility is checked before the existing order rules run."""
    if not qualified(card):
        return []
    view = flow.View.of(events)
    oid = order_id_for(comment["comment_id"])
    if view.order(oid):
        return []
    draft_in = label.get("order_draft") or {}
    qty, notes = flow.draft_qty(draft_in, [])
    want = variants.want_of(draft_in)
    hits = variants.hit_indexes(card, want)
    blocking = ["seller must select one frozen attachment candidate"] if candidates else []
    chosen = dict(want)
    if len(hits) == 1:
        variant = card["variants"][hits[0]]
        chosen, stock = variants.variant_of(variant), variant.get("stock") or 0
    elif not card["variants"]:
        stock = card.get("stock") or 0
    else:
        missing = variants.missing_fields(card, hits, want)
        if missing and not candidates:
            asked = await flow.ask_variant(
                attachment["event_id"],
                view,
                SimMessenger(),
                now,
                comment,
                card,
                oid,
                hits,
                missing,
                [attachment["event_id"], comment["event_id"], label["event_id"], card["event_id"]],
            )
            if asked is not None:
                return await append_partials(store, comment["session_id"], comment["source"], asked)
        stock = card.get("stock") or 0
        blocking.append("variant is unavailable or unresolved on the selected card")
    partials = await flow.draft_order(
        attachment["event_id"],
        view,
        SimMessenger(),
        now,
        comment,
        card,
        oid,
        chosen,
        stock,
        qty,
        notes,
        blocking,
        [attachment["event_id"], comment["event_id"], label["event_id"], card["event_id"]],
        reply_to=comment["comment_id"],
        seller_picks_card=bool(candidates),
    )
    if candidates:
        for partial in partials:
            if partial["type"] == "order":
                partial["lines"] = [{**line, "candidates": list(candidates)} for line in partial["lines"]]
    if not persist:
        if not candidates:
            raise ValueError("only the frozen candidate expiry can be prepared without persistence")
        return build_partials(comment["session_id"], comment["source"], partials)
    return await append_partials(
        store, comment["session_id"], comment["source"], partials, intent=(comment["comment_id"], oid)
    )


def comment_positions(view):
    """Each comment-born order's id to the stored position of the comment it comes from."""
    return {
        order_id_for(cid): row["comment"]["seq"]
        for cid, row in view.comments.items()
        if row.get("comment") and "seq" in row["comment"]
    }


async def unresolved_comments(store, sid, events, now):
    """The comments whose canonical order label has no attachment yet and is still inside the binding's wait.
    The app's own proof or expiry of each one ends in a reconcile pass, so an input deferred behind one of
    them is judged again then, against the orders that attachment creates."""
    from app.attachments import eligible_labels

    attached = {e["comment_id"] for e in events if e["type"] == "comment_attachment"}
    waiting = {cid: pair for cid, pair in eligible_labels(events).items() if cid not in attached}
    if not waiting:
        return {}
    binding = await store.attachment_binding(sid)
    if binding is None:
        return {}
    receipts = await store.source_receipts(sid)
    return {
        cid: comment
        for cid, (comment, label) in waiting.items()
        if label["event_id"] in receipts and now < comment["ingested_ms"] + binding["wait_ms"]
    }


async def reconcile(store, sid, now, events=None):
    """Rebuild continuations from durable decisions; callers hold the session transaction."""
    events = list(await store.attachment_events(sid) if events is None else events)
    by_id = {e["event_id"]: e for e in events}
    out = []
    for attachment in [e for e in events if e["type"] == "comment_attachment"]:
        if attachment["action"] == "reject":
            continue
        comment, label = by_id[attachment["comment_event_id"]], by_id[attachment["label_event_id"]]
        oid = order_id_for(comment["comment_id"])
        if any(e["type"] == "order" and e["order_id"] == oid for e in events):
            continue
        target = attachment["selected_card_id"]
        if target:
            if attachment["action"] == "keep_unresolved":
                attachment = await store.attachment_selection(sid, attachment["attachment_id"])
            card = latest_card(events, target)
            if not qualified(card):
                continue
            # C3 readers see a reconciled reference, derived from the actual frozen attachment.
            reconciled_id = flow.step_id(attachment["event_id"], oid, "reference")
            if reconciled_id not in by_id:
                resolved = {k: copy.deepcopy(v) for k, v in label.items() if k not in {"seq", "ingested_ms"}}
                resolved.update(
                    event_id=reconciled_id,
                    ts_ms=now,
                    caused_by=[attachment["event_id"], label["event_id"], comment["event_id"]],
                    latency_ms=max(now - comment["ingested_ms"], 0),
                )
                resolved["order_draft"].update(referenced_card_id=target, sku=card["sku"])
                resolved["timing"] = {
                    **label.get("timing", {}),
                    "received_ms": comment["ingested_ms"],
                    "emitted_ms": now,
                }
                await store.append(resolved)
                out.append(resolved)
                events.append(resolved)
                by_id[resolved["event_id"]] = resolved
            added = await draft_attachment(store, attachment, events, comment, label, card, now)
            out += added
            events += added
        elif attachment["outcome"] == "expired" and len(attachment["candidates"]) == 2:
            frozen = [by_id[c["card_event_id"]] for c in attachment["candidates"]]
            if all(qualified(c) for c in frozen):
                added = await draft_attachment(
                    store,
                    attachment,
                    events,
                    comment,
                    label,
                    frozen[0],
                    now,
                    [c["card_id"] for c in attachment["candidates"]],
                )
                out += added
                events += added
    # Replies are consumed here, never by a credentialed worker's generic event path.
    source_comments = {event["comment_id"]: event for event in events if event["type"] == "comment"}
    inputs = []
    for event in events:
        if event["type"] == "message_in":
            inputs.append(event)
        elif event["type"] == "comment_labeled" and event.get("label") in QUESTION_LABELS:
            inputs.append(event)  # a price or stock question about the item on screen gets one private reply
        elif event["type"] == "comment_labeled" and event.get("label") in flow.TRIGGER_LABELS:
            cancellation = flow.cancel_request((source_comments.get(event["comment_id"]) or {}).get("text"))
            if cancellation and cancellation["kind"] == "cancel":
                inputs.append(event)
    if not inputs:
        return out
    consumed = await store.consumed_attachment_inputs(sid)
    waiting = None
    for event in inputs:
        if event["event_id"] in consumed:
            continue
        if event["type"] not in {"message_in", "comment_labeled"}:
            continue
        view = flow.View.of(events)
        if event["type"] == "message_in":
            partials = await reply_partials(store, sid, event, view, events, now)
        elif event.get("label") in QUESTION_LABELS:
            partials, _ = await on_question(event, events, SimMessenger(), now)
        elif event.get("label") in flow.TRIGGER_LABELS:
            row = view.comments.get(event["comment_id"]) or {}
            comment = row.get("comment") or {}
            cancellation = flow.cancel_request(comment.get("text"))
            if not cancellation or cancellation["kind"] != "cancel":
                continue
            if waiting is None:
                waiting = await unresolved_comments(store, sid, events, now)
            cancel_at = comment.get("seq")
            if cancel_at is not None and any(
                earlier["user_ref"] == comment.get("user_ref")
                and earlier.get("seq", float("inf")) < cancel_at
                for earlier in waiting.values()
            ):
                continue  # judged again once that attachment is proved or expires, against the orders it creates
            # An order from an earlier comment counts even when it was stored after the cancel label.
            origins = comment_positions(view)
            view.history = {
                oid: history
                for oid, history in view.history.items()
                if (
                    origins[oid] < cancel_at
                    if oid in origins and cancel_at is not None
                    else history[0].get("seq", float("inf")) < event["seq"]
                )
            }
            partials = await flow.on_labeled(event, view, SimMessenger(), now)
        else:
            continue
        existing = {e["event_id"] for e in events}
        fresh = [p for p in partials if p["event_id"] not in existing]
        if fresh:
            added = await append_partials(store, sid, event["source"], fresh)
            events += added
            out += added
        await store.consume_attachment_input(sid, event["event_id"])
    return out


async def decide(store, sid, order_id, body, now):
    """Explicit candidate selection on an existing order uses the existing approve/reject authority."""
    events = await store.attachment_events(sid)
    view = flow.View.of(events)
    order = view.order(order_id)
    if order is None:
        raise HTTPException(404, "order is not in this session")
    if order["state"] != "NEEDS_SELLER":
        raise HTTPException(409, "order is not awaiting a seller decision")
    line = order["lines"][0]
    candidates = line.get("candidates")
    card_id = body.get("card_id")
    if candidates and body["decision"] == "approve":
        if not card_id or card_id not in candidates:
            raise HTTPException(422, "approval requires an explicit candidate from this order")
        attachment = next(
            (
                e
                for e in events
                if e["type"] == "comment_attachment" and order_id_for(e["comment_id"]) == order_id
            ),
            None,
        )
        if not attachment or card_id not in {c["card_id"] for c in attachment["candidates"]}:
            raise HTTPException(422, "candidate is outside the frozen same-session attachment")
        card = latest_card(events, card_id)
        if not qualified(card):
            raise HTTPException(409, "selected card has no current SKU and approved price")
        want = line["variant"]
        hits = variants.hit_indexes(card, want)
        stock = (card["variants"][hits[0]].get("stock") or 0) if len(hits) == 1 else (card.get("stock") or 0)
        if card["variants"] and len(hits) != 1:
            raise HTTPException(409, "variant is not unique on the selected card")
        qty = line["qty"]
        free = max(stock - flow.held(view, card_id, want, order_id), 0)
        price = card["price"] if body.get("unit_price") is None else body["unit_price"]
        check = rules.evaluate_draft(price, price, qty, free)
        if qty < 1 or price < 0 or qty > free:
            raise HTTPException(409, check.reason)
        picked = {
            **line,
            "card_id": card_id,
            "card_version": card["version"],
            "sku": card["sku"],
            "unit_price": card["price"],
        }
        order = {**order, "lines": [picked]}
        view.history[order_id] = [*view.history[order_id][:-1], order]
    elif card_id is not None:
        raise HTTPException(422, "card choice is not applicable to this decision")
    try:
        partials = await flow.decide(
            view, order_id, body["decision"], SimMessenger(), now, body.get("unit_price")
        )
    except flow.FlowError as exc:
        raise HTTPException(409, str(exc)) from exc
    if candidates and body["decision"] == "approve":
        for partial in partials:
            if partial["type"] == "order":
                partial["caused_by"] += [attachment["event_id"], card["event_id"]]
    return await append_partials(store, sid, order["source"], partials)
