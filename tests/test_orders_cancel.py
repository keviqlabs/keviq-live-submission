"""Cancel requests: a comment (label order, other or complaint) or a plain "huỷ" leads to an ask_cancel question in
the sim Messenger, once per comment; only the customer's own CANCEL:<order_id> quick reply moves the order to
CANCELLED, and a plain yes to the question is asked again instead of confirming the order, also when another plain
message comes between them or the yes follows the customer's own plain "huỷ" before its question is stored."""

import json

import pytest

from app.contracts import now_ms
from app.settings import ROOT
from connectors.sim import replay
from connectors.sim.messenger import webhook_body
from orders import flow, handlers
from orders.intent import cancel_request

SID = "t1"
LINE = {
    "sku": "X-1",
    "card_id": "card-x",
    "card_version": 2,
    "variant": {"size": "39", "color": "đen"},
    "qty": 1,
    "unit_price": 300000,
}
CARD = {
    "event_id": "card-x2",
    "type": "card",
    "ts_ms": 1000,
    "card_id": "card-x",
    "version": 2,
    "status": "live",
    "sku": "X-1",
    "name": "Giày X",
    "price": 300000,
    "image_url": "sim://x.png",
    "variants": [{"size": "39", "color": "đen", "stock": 5}],
}


class Recorder:
    def __init__(self):
        self.sent = []

    async def send(self, thread_ref, template, payload):
        self.sent.append((thread_ref, template, payload))
        return {"type": "message_out", "thread_ref": thread_ref, "template": template, "payload": payload}


def order(oid: str, state: str, ts: int = 1000, customer: str = "ux") -> dict:
    return {
        "event_id": f"ev.{oid}.{state}",
        "type": "order",
        "ts_ms": ts,
        "order_id": oid,
        "customer_ref": customer,
        "lines": [LINE],
        "amount": 300000,
        "state": state,
        "prev_state": None,
        "actor": "assistant",
        "approval_needed": state == "NEEDS_SELLER",
        "reason": "test",
        "receipt": None,
        "caused_by": [],
    }


def comment_scene(text: str, orders: list[dict], label: str = "other", user: str = "ux", ts: int = 5000):
    comment = {
        "event_id": "cm-c",
        "type": "comment",
        "ts_ms": ts,
        "comment_id": "cm-c",
        "user_ref": user,
        "text": text,
    }
    draft = {"variant": {"size": "39", "color": None}, "qty": 1, "referenced_card_id": "card-x"}
    labeled = {
        "event_id": "lb-c",
        "type": "comment_labeled",
        "ts_ms": ts,
        "comment_id": "cm-c",
        "label": label,
        "order_draft": draft if label == "order" else None,
    }
    return labeled, flow.View.of([CARD, *orders, comment, labeled])


def message_scene(payload: dict, orders: list[dict], template=None, thread: str = "th-ux", ts: int = 5000):
    mi = {
        "event_id": "mi-c",
        "type": "message_in",
        "ts_ms": ts,
        "thread_ref": thread,
        "template": template,
        "payload": payload,
    }
    return mi, flow.View.of([CARD, *orders, mi])


def said(eid: str, text: str, ts: int, thread: str = "th-ux") -> dict:
    return {
        "event_id": eid,
        "type": "message_in",
        "ts_ms": ts,
        "thread_ref": thread,
        "template": None,
        "payload": {"text": text},
    }


def quick_replies(event: dict) -> list[str]:
    return [q[1] for q in event["payload"]["quick_replies"]]


# --- the 10 cases ------------------------------------------------------------------------------------------


async def post_comment(client, runner, user: str, text: str, label: str = "other", n: int = 90) -> list[dict]:
    """A new comment and its label for `user` in session t1, posted and handled as the worker would."""
    cm = {
        "event_id": f"cm-{n}",
        "ts_ms": 0,
        "source": "replay",
        "v": 1,
        "type": "comment",
        "comment_id": f"cm-{n}",
        "user_ref": user,
        "text": text,
        "reply_to": None,
    }
    lb = {
        "event_id": f"lb-{n}",
        "ts_ms": 0,
        "source": "replay",
        "v": 1,
        "type": "comment_labeled",
        "comment_id": f"cm-{n}",
        "label": label,
        "order_draft": None,
        "scam_signals": [],
        "action": "none",
        "latency_ms": 500,
        "caused_by": [f"cm-{n}"],
    }
    out = []
    for _, e in replay.plan([cm, lb], SID, start_ms=now_ms()):
        assert (await client.post(f"/api/sessions/{SID}/events", json=[e])).status_code == 200
        out += await runner.handle(e)
    assert runner.failures == 0
    return out


async def reply(client, runner, thread: str, quick_reply: str, ts: int) -> list[dict]:
    body = webhook_body(thread, ts, text="x", quick_reply=quick_reply)
    [mi] = (await client.post(f"/api/sessions/{SID}/sim/messenger", json=body)).json()["events"]
    return await runner.handle(mi)


async def stored(client, thread: str, text: str, ts: int) -> dict:
    """A customer's text in the sim Messenger, stored but not handled yet."""
    body = webhook_body(thread, ts, text=text)
    [mi] = (await client.post(f"/api/sessions/{SID}/sim/messenger", json=body)).json()["events"]
    return mi


async def say(client, runner, thread: str, text: str, ts: int) -> list[dict]:
    return await runner.handle(await stored(client, thread, text, ts))


async def history(store, order_id: str) -> list[dict]:
    return [e for e in await store.read(SID, 0, 2000) if e["type"] == "order" and e["order_id"] == order_id]


@pytest.fixture
async def played(mem_app, demo_events):
    from tests.test_orders_flow import play, without_fixture_orders

    _, store, client = mem_app
    runner, _ = await play(client, without_fixture_orders(demo_events))
    assert [o["state"] for o in await history(store, "o-07")][-1] == "CONFIRM_SENT"
    return runner, store, client


@pytest.mark.parametrize("label", ["other", "complaint"])  # the model may call a plain cancel a complaint
async def test_one_open_order_is_asked_then_cancelled_on_its_quick_reply(played, label):
    runner, store, client = played
    [ask] = await post_comment(client, runner, "u07", "huỷ đơn giúp mình nha shop", label=label)
    assert (ask["type"], ask["template"], ask["thread_ref"]) == ("message_out", "ask_cancel", "th-u07")
    assert ask["payload"]["order_id"] == "o-07" and "o-07" in ask["payload"]["text"]
    assert ask["payload"]["send_api"]["recipient"] == {"comment_id": "cm-90"}  # private reply to the comment
    assert [q["payload"] for q in ask["payload"]["send_api"]["message"]["quick_replies"]] == [
        "CANCEL:o-07",
        "KEEP:o-07",
    ]
    assert (await history(store, "o-07"))[-1]["state"] == "CONFIRM_SENT"  # asking changes nothing

    cancelled, msg = await reply(client, runner, "th-u07", "CANCEL:o-07", now_ms())
    assert (cancelled["state"], cancelled["prev_state"], cancelled["actor"]) == (
        "CANCELLED",
        "CONFIRM_SENT",
        "customer",
    )
    assert msg["template"] == "order_cancelled" and msg["caused_by"][-1] == cancelled["event_id"]


@pytest.mark.parametrize("label", ["other", "complaint"])
async def test_keep_changes_nothing(played, label):
    runner, store, client = played
    [ask] = await post_comment(client, runner, "u07", "huỷ đơn giúp mình nha shop", label=label)
    assert ask["template"] == "ask_cancel"  # the question went out, so KEEP answers it
    before = await history(store, "o-07")
    assert await reply(client, runner, "th-u07", "KEEP:o-07", now_ms()) == []
    assert await history(store, "o-07") == before


@pytest.mark.parametrize(
    "text",
    ["đừng huỷ đơn nha shop", "không huỷ đâu", "khỏi huỷ", "ko huỷ nha", "k huỷ nha", "có nên huỷ không ta"],
)
async def test_negation_or_ambiguous_text_sends_nothing(text):
    label, view = comment_scene(text, [order("o-1", "CONFIRM_SENT")])
    rec = Recorder()
    assert await flow.on_labeled(label, view, rec, now=6000) == []
    assert rec.sent == []


async def test_no_open_order_sends_nothing():
    label, view = comment_scene("huỷ đơn nha shop", [])
    rec = Recorder()
    assert await flow.on_labeled(label, view, rec, now=6000) == []
    assert rec.sent == []


@pytest.mark.parametrize("state", ["CANCELLED", "CLOSED"])
async def test_terminal_order_sends_nothing_and_cannot_be_cancelled(state):
    rec = Recorder()
    label, view = comment_scene("huỷ đơn nha shop", [order("o-1", state)])
    assert await flow.on_labeled(label, view, rec, now=6000) == []
    mi, view = message_scene(
        {"order_id": "o-1", "quick_reply": "CANCEL:o-1"}, [order("o-1", state)], "confirm_no"
    )
    assert await flow.on_reply(mi, view, rec, now=6000) == []
    assert rec.sent == []


async def test_two_open_orders_ask_which_one_and_pick_none():
    mine = [order("o-1", "CONFIRM_SENT", ts=1000), order("o-2", "NEEDS_SELLER", ts=2000)]
    label, view = comment_scene("huỷ đơn nha shop", [*mine, order("o-9", "CONFIRM_SENT", customer="uy")])
    [ask] = await flow.on_labeled(label, view, Recorder(), now=6000)
    assert ask["template"] == "ask_cancel" and ask["payload"]["order_ids"] == ["o-1", "o-2"]
    assert quick_replies(ask) == ["CANCEL:o-1", "CANCEL:o-2", "KEEP:all"]
    assert all(len(title) <= 20 for title, _ in ask["payload"]["quick_replies"])  # Send API title limit
    assert "o-9" not in ask["payload"]["text"]

    mi, view = message_scene({"text": "Huỷ"}, mine)  # a plain "huỷ" in Messenger picks no order either
    [ask] = await flow.on_reply(mi, view, Recorder(), now=6000)
    assert ask["template"] == "ask_cancel" and quick_replies(ask) == ["CANCEL:o-1", "CANCEL:o-2", "KEEP:all"]


async def test_c140_scam_text_never_asks():
    c140 = next(
        json.loads(x)
        for x in (ROOT / "data" / "comments" / "labels.jsonl").read_text().splitlines()
        if x.strip() and json.loads(x)["comment_id"] == "c140"
    )
    assert c140["label"] == "scam" and cancel_request(c140["text"])["kind"] == "unclear"
    for label in ("scam", "spam"):  # the handler does not even read the session
        assert (
            await handlers.on_labeled({"event_id": "lb-c", "label": label, "comment_id": "cm-c"}, None) == []
        )
    rec = Recorder()
    labeled, view = comment_scene(c140["text"], [order("o-1", "CONFIRM_SENT")], label="other")
    assert await flow.on_labeled(labeled, view, rec, now=6000) == []  # even if it were mislabeled
    assert rec.sent == []


async def test_repeated_cancel_is_idempotent(played):
    runner, store, client = played
    first = await reply(client, runner, "th-u07", "CANCEL:o-07", now_ms())
    assert [e.get("state") or e["template"] for e in first] == ["CANCELLED", "order_cancelled"]
    assert await reply(client, runner, "th-u07", "CANCEL:o-07", now_ms() + 5) == []
    events = await store.read(SID, 0, 2000)
    assert [e["state"] for e in events if e["type"] == "order" and e["order_id"] == "o-07"].count(
        "CANCELLED"
    ) == 1
    cancelled_msgs = [e for e in events if e["type"] == "message_out" and e["template"] == "order_cancelled"]
    assert [e["event_id"] for e in cancelled_msgs] == [first[1]["event_id"]]


async def test_cancel_from_another_customers_thread_is_refused(played):
    runner, store, client = played
    before = await history(store, "o-07")
    assert await reply(client, runner, "th-u99", "CANCEL:o-07", now_ms()) == []
    assert await history(store, "o-07") == before
    # and another customer's cancel comment is not asked about this customer's order
    assert await post_comment(client, runner, "u99", "huỷ đơn giúp mình") == []


async def test_order_label_with_cancel_text_makes_no_draft():
    label, view = comment_scene("huỷ đơn đôi 39", [], label="order")
    assert await flow.on_labeled(label, view, Recorder(), now=6000) == []
    label, view = comment_scene("huỷ đơn đôi 39", [order("o-1", "CONFIRM_SENT")], label="order")
    [ask] = await flow.on_labeled(label, view, Recorder(), now=6000)
    assert ask["template"] == "ask_cancel" and quick_replies(ask) == ["CANCEL:o-1", "KEEP:o-1"]


async def test_complaint_label_with_cancel_text_is_asked_and_changes_no_order():
    label, view = comment_scene(
        "huỷ đơn giúp mình nha shop", [order("o-1", "CONFIRM_SENT")], label="complaint"
    )
    rec = Recorder()
    [ask] = await flow.on_labeled(label, view, rec, now=6000)  # a question only: no order event
    assert (ask["type"], ask["template"]) == ("message_out", "ask_cancel")
    assert quick_replies(ask) == ["CANCEL:o-1", "KEEP:o-1"]
    assert [template for _, template, _ in rec.sent] == ["ask_cancel"]
    label, view = comment_scene("huỷ đơn giúp mình nha shop", [], label="complaint")
    assert await flow.on_labeled(label, view, Recorder(), now=6000) == []


@pytest.mark.parametrize(
    "text",
    [
        "đừng huỷ đơn nha shop",
        "ko huỷ nha",
        "có nên huỷ không ta",
        "lần trước bị huỷ đơn rồi",
        "giao chậm quá shop",
    ],
)
async def test_complaint_label_without_a_clear_cancel_sends_nothing_and_never_drafts(text):
    label, view = comment_scene(text, [order("o-1", "CONFIRM_SENT")], label="complaint")
    label["order_draft"] = {
        "variant": {"size": "39", "color": None},
        "qty": 1,
        "referenced_card_id": "card-x",
    }
    rec = Recorder()
    assert await flow.on_labeled(label, view, rec, now=6000) == []
    assert rec.sent == []


@pytest.mark.parametrize("label", ["scam", "spam"])
async def test_scam_or_spam_label_with_cancel_text_never_asks(label):
    labeled, view = comment_scene("huỷ đơn giúp mình nha shop", [order("o-1", "CONFIRM_SENT")], label=label)
    rec = Recorder()
    assert await flow.on_labeled(labeled, view, rec, now=6000) == []
    assert rec.sent == []


@pytest.mark.parametrize(
    "text",
    ["Chốt 1 đôi size 39, ko huỷ nha", "chốt đôi 39 k huỷ nha", "không huỷ đâu", "lần trước bị huỷ đơn rồi"],
)
async def test_order_label_with_negated_or_unclear_cancel_words_still_drafts(text):
    label, view = comment_scene(text, [], label="order")
    rec = Recorder()
    out = await flow.on_labeled(label, view, rec, now=6000)
    states = [e["state"] for e in out if e["type"] == "order"]
    assert states == ["DRAFT", "CONFIRM_SENT"] and "CANCELLED" not in states
    assert [template for _, template, _ in rec.sent] == ["confirm_order"]


# --- around the cases --------------------------------------------------------------------------------------


async def test_customer_may_cancel_while_waiting_for_the_seller(played):
    runner, store, client = played
    assert (await history(store, "o-05"))[-1]["state"] == "NEEDS_SELLER"
    cancelled, _ = await reply(client, runner, "th-u05", "CANCEL:o-05", now_ms())
    assert (cancelled["state"], cancelled["actor"]) == ("CANCELLED", "customer")


async def test_plain_text_no_asks_instead_of_cancelling():
    mi, view = message_scene({"text": "không"}, [order("o-1", "CONFIRM_SENT")])
    [ask] = await flow.on_reply(mi, view, Recorder(), now=6000)
    assert ask["template"] == "ask_cancel" and quick_replies(ask) == ["CANCEL:o-1", "KEEP:o-1"]


async def test_no_question_outside_the_24h_window():
    label, view = comment_scene("huỷ đơn nha shop", [order("o-1", "CONFIRM_SENT")])
    rec = Recorder()
    assert await flow.on_labeled(label, view, rec, now=5000 + 25 * 3600 * 1000) == []
    assert rec.sent == []


async def test_a_replayed_cancel_comment_is_asked_once():
    label, view = comment_scene("huỷ đơn nha shop", [order("o-1", "CONFIRM_SENT")])
    [ask] = await flow.on_labeled(label, view, Recorder(), now=6000)
    assert ask["event_id"] == flow.step_id("lb-c", "o-1", "ask-cancel")
    view.messages.append(ask)
    rec = Recorder()
    assert await flow.on_labeled(label, view, rec, now=7000) == [] and rec.sent == []


async def test_a_second_label_of_the_same_comment_is_not_asked_again():
    # The labeler may post an interim label and then the batch label of one comment: one question per comment.
    label, view = comment_scene("huỷ đơn nha shop", [order("o-1", "CONFIRM_SENT")])
    [ask] = await flow.on_labeled(label, view, Recorder(), now=6000)
    view.messages.append(ask)
    for second in ("complaint", "other", "order"):
        relabeled = {**label, "event_id": f"lb-c-{second}", "label": second}
        rec = Recorder()
        assert await flow.on_labeled(relabeled, view, rec, now=7000) == [] and rec.sent == []
    # a new comment is a new question
    again = {**view.comments["cm-c"]["comment"], "event_id": "cm-d", "comment_id": "cm-d", "ts_ms": 6500}
    view.comments["cm-d"] = {"comment": again, "labeled": None}
    [ask] = await flow.on_labeled(
        {**label, "event_id": "lb-d", "comment_id": "cm-d"}, view, Recorder(), now=7000
    )
    assert ask["template"] == "ask_cancel" and ask["payload"]["comment_id"] == "cm-d"


@pytest.mark.parametrize("text", ["có", "ok"])
async def test_a_plain_yes_to_the_cancel_question_is_asked_again(text):
    label, view = comment_scene("huỷ đơn nha shop", [order("o-1", "CONFIRM_SENT")], label="complaint")
    [ask] = await flow.on_labeled(label, view, Recorder(), now=6000)
    mi = said("mi-c", text, 7000)
    view.messages += [ask, mi]
    rec = Recorder()
    [again] = await flow.on_reply(mi, view, rec, now=7000)  # no order event: the order stays CONFIRM_SENT
    assert again["template"] == "ask_cancel" and quick_replies(again) == ["CANCEL:o-1", "KEEP:o-1"]
    assert [template for _, template, _ in rec.sent] == ["ask_cancel"]


@pytest.mark.parametrize("second", ["có", "ok"])
async def test_two_plain_yeses_stored_before_either_is_handled_are_both_asked_again(second):
    # "có có", or a worker behind: the question asked again for the first yes is stored after the second yes
    label, view = comment_scene("huỷ đơn nha shop", [order("o-1", "CONFIRM_SENT")], label="complaint")
    [ask] = await flow.on_labeled(label, view, Recorder(), now=6000)
    first, then = said("mi-1", "có", 7000), said("mi-2", second, 7001)
    view.messages += [ask, first, then]
    for yes in (first, then):
        [again] = await flow.on_reply(yes, view, Recorder(), now=7002)
        assert again["template"] == "ask_cancel" and quick_replies(again) == ["CANCEL:o-1", "KEEP:o-1"]
        view.messages.append(again)


async def test_a_plain_yes_after_other_text_to_the_cancel_question_is_asked_again():
    label, view = comment_scene("huỷ đơn nha shop", [order("o-1", "CONFIRM_SENT")], label="complaint")
    [ask] = await flow.on_labeled(label, view, Recorder(), now=6000)
    text, yes = said("mi-1", "sao vậy shop", 7000), said("mi-2", "có", 7001)
    view.messages += [ask, text]
    assert await flow.on_reply(text, view, Recorder(), now=7000) == []
    view.messages.append(yes)
    [again] = await flow.on_reply(yes, view, Recorder(), now=7001)
    assert again["template"] == "ask_cancel" and quick_replies(again) == ["CANCEL:o-1", "KEEP:o-1"]


@pytest.mark.parametrize("cancel", ["huỷ", "không"])
async def test_a_plain_yes_stored_before_the_question_for_a_plain_cancel_is_asked_again(cancel):
    # "huỷ" then "có" before the question for "huỷ" is stored: the yes does not confirm the order
    mi, view = message_scene({"text": cancel}, [order("o-1", "CONFIRM_SENT")])
    yes = said("mi-2", "có", 5001)
    view.messages.append(yes)
    [ask] = await flow.on_reply(mi, view, Recorder(), now=5002)
    assert ask["template"] == "ask_cancel"
    view.messages.append(ask)
    [again] = await flow.on_reply(yes, view, Recorder(), now=5003)
    assert again["template"] == "ask_cancel" and quick_replies(again) == ["CANCEL:o-1", "KEEP:o-1"]


@pytest.mark.parametrize("text", ["có", "ok"])
@pytest.mark.parametrize("label", ["other", "complaint"])
async def test_a_plain_yes_after_the_cancel_question_confirms_nothing(played, label, text):
    runner, store, client = played
    await post_comment(client, runner, "u07", "huỷ đơn giúp mình nha shop", label=label)
    [again] = await say(client, runner, "th-u07", text, now_ms())
    assert again["template"] == "ask_cancel"
    assert [q["payload"] for q in again["payload"]["send_api"]["message"]["quick_replies"]] == [
        "CANCEL:o-07",
        "KEEP:o-07",
    ]
    assert (await history(store, "o-07"))[-1]["state"] == "CONFIRM_SENT"


@pytest.mark.parametrize("label", ["other", "complaint"])
async def test_two_plain_yeses_stored_before_the_worker_handles_them_confirm_nothing(played, label):
    runner, store, client = played
    await post_comment(client, runner, "u07", "huỷ đơn giúp mình nha shop", label=label)
    t = now_ms()
    first, then = await stored(client, "th-u07", "có", t), await stored(client, "th-u07", "ok", t + 1)
    for yes in (first, then):
        [again] = await runner.handle(yes)
        assert again["template"] == "ask_cancel"
    assert runner.failures == 0
    assert (await history(store, "o-07"))[-1]["state"] == "CONFIRM_SENT"


async def test_a_plain_yes_after_other_text_confirms_nothing(played):
    runner, store, client = played
    await post_comment(client, runner, "u07", "huỷ đơn giúp mình nha shop", label="complaint")
    assert await say(client, runner, "th-u07", "sao vậy shop", now_ms()) == []
    [again] = await say(client, runner, "th-u07", "có", now_ms() + 5)
    assert again["template"] == "ask_cancel"
    assert (await history(store, "o-07"))[-1]["state"] == "CONFIRM_SENT"


async def test_a_plain_yes_stored_with_a_plain_cancel_confirms_nothing(played):
    runner, store, client = played
    t = now_ms()
    cancel, yes = await stored(client, "th-u07", "huỷ", t), await stored(client, "th-u07", "có", t + 1)
    for mi in (cancel, yes):
        [ask] = await runner.handle(mi)
        assert ask["template"] == "ask_cancel"
    assert runner.failures == 0
    assert (await history(store, "o-07"))[-1]["state"] == "CONFIRM_SENT"


async def test_the_confirm_button_still_confirms_after_the_cancel_question(played):
    runner, store, client = played
    await post_comment(client, runner, "u07", "huỷ đơn giúp mình nha shop", label="complaint")
    confirmed, msg = await reply(client, runner, "th-u07", "CONFIRM:o-07", now_ms())
    assert (confirmed["state"], confirmed["actor"], msg["template"]) == ("CONFIRMED", "customer", "receipt")


async def test_a_plain_yes_after_keep_confirms(played):
    runner, store, client = played
    await post_comment(client, runner, "u07", "huỷ đơn giúp mình nha shop", label="complaint")
    assert await reply(client, runner, "th-u07", "KEEP:o-07", now_ms()) == []
    confirmed, msg = await say(client, runner, "th-u07", "ok", now_ms() + 5)
    assert (confirmed["state"], confirmed["actor"], msg["template"]) == ("CONFIRMED", "customer", "receipt")
