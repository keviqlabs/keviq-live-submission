"""A cancel comment carrying a phone number gets two labels from the real labeler: the shield's interim `other` at
once, then the batch label. The customer is still asked once per comment, in a session without and with an
attachment binding, and a plain yes to the question is asked again instead of confirming the order, also when two
arrive before the session is reconciled or when a plain text carrying the order's id came first.

The fake model answers from the comment text (no model call)."""

import json

import pytest

from app.contracts import make_event, now_ms
from connectors.sim.moderator import SimModerator
from meta.client import MetaClient
from meta.meter import Meter, memory_spend
from meta.transport import responses_reply
from orders import flow
from orders import handlers as orders_handlers
from tests.test_attachments_runtime import world  # noqa: F401  (the bound-session fixture)
from tests.test_labels import card_event, drain, feed, wire
from tests.test_labels_causal import ProvingRunner, comment, quick_replies, stored_labels
from worker.brain import labels as labels_mod
from worker.brain.labels import Labeler
from worker.runner import Runner, build

ORDER = "chốt đôi này size {size}"
CANCEL = "shop giao chậm quá, huỷ đơn đi, gọi 0900 000 012"  # a fake number


class Transport:
    """Labels a comment that starts with "chốt" as an order for the current card (size `size`, qty 1) and any
    other comment as `cancel_label`."""

    def __init__(self, cancel_label: str, size: str):
        self.cancel_label, self.size = cancel_label, size

    async def post(self, path: str, body: dict):
        comments = json.loads(body["input"][1]["content"])["comments"]
        items = []
        for c in comments:
            order = c["text"].startswith("chốt")
            items.append(
                {
                    "comment_id": c["comment_id"],
                    "label": "order" if order else self.cancel_label,
                    "reference_kind": "current_card" if order else "none",
                    "sku": None,
                    "size": self.size if order else None,
                    "color": None,
                    "qty": 1 if order else None,
                    "contact": "none",
                }
            )
        return responses_reply(json.dumps({"items": items}, ensure_ascii=False), 900, 300)

    async def aclose(self):
        pass


def asks(events: list[dict]) -> list[dict]:
    return [e for e in events if e["type"] == "message_out" and e["template"] == "ask_cancel"]


@pytest.mark.parametrize("cancel_label", ["complaint", "other", "order"])
async def test_unbound_two_labels_of_one_cancel_comment_ask_once(mem_app, monkeypatch, cancel_label):
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.05)
    _, store, client = mem_app
    sid, now = f"two-labels-{cancel_label}", now_ms()
    labeler, runner = wire(client, Transport(cancel_label, "40"))
    card = card_event(sid, "card-1", 1, "live", 150_000, now - 60_000, now - 60_000)
    order = comment(sid, "c-order", "u-9", ORDER.format(size="40"), now - 30_000)
    await feed(client, runner, [(0, card), (0, order)])
    await drain(labeler)
    await feed(client, runner, [(0, comment(sid, "c-cancel", "u-9", CANCEL, now - 20_000))])
    await drain(labeler)
    labels = await stored_labels(store, sid)
    assert [(e["comment_id"], e["label"]) for e in labels] == [
        ("c-order", "order"),
        ("c-cancel", "other"),
        ("c-cancel", cancel_label),
    ]

    orders = Runner("http://test", orders_handlers.HANDLERS, http=client)
    for label in labels:
        await orders.handle(label)
    assert orders.failures == 0
    events = await store.read(sid, 0, 500)
    oid = flow.order_id_for("c-order")
    assert [quick_replies(a) for a in asks(events)] == [[f"CANCEL:{oid}", f"KEEP:{oid}"]]
    assert [e["state"] for e in events if e["type"] == "order"] == ["DRAFT", "CONFIRM_SENT"]


async def bound_order_then_cancel(w, monkeypatch, cancel_label: str) -> None:
    """The order comment is labeled and proved (CONFIRM_SENT), then the cancel comment gets its two labels and the
    reconcile pass runs."""
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.05)
    await w.card(10, "GIAY-01", 150_000)
    transport = Transport(cancel_label, "38")
    holder = {}

    def client_for(ctx):
        async def emit(fields):
            await holder["runner"].post([build(ctx.event, {"type": "model_call", **fields}, now_ms())])

        return MetaClient(transport, Meter(memory_spend([0.0]), 30, 300, 20), emit)

    labeler = Labeler(client_for=client_for, moderator=SimModerator())
    holder["runner"] = runner = ProvingRunner(w, labeler.handlers())
    assert await runner.attachment_enabled(w.sid) is True
    order = comment(w.sid, "c-order", "synthetic-buyer", ORDER.format(size="38"), 25)
    await w.ingest(order)
    await w.frame(30)
    await runner.handle(order)
    await drain(labeler)
    cancel = comment(w.sid, "c-cancel", "synthetic-buyer", CANCEL, 40)
    await w.ingest(cancel)
    await runner.handle(cancel)
    await drain(labeler)
    await w.service.expire_due(w.sid)


@pytest.mark.parametrize("cancel_label", ["complaint", "other"])
async def test_bound_two_labels_of_one_cancel_comment_ask_once(world, monkeypatch, cancel_label):  # noqa: F811
    w = world
    await bound_order_then_cancel(w, monkeypatch, cancel_label)
    await w.service.expire_due(w.sid)  # a second pass asks nothing new
    events = await w.events()
    labels = [e["label"] for e in events if e["type"] == "comment_labeled" and e["comment_id"] == "c-cancel"]
    assert labels == ["other", cancel_label]
    orders = [e for e in events if e["type"] == "order"]
    assert [e["state"] for e in orders] == ["DRAFT", "CONFIRM_SENT"]
    oid = orders[0]["order_id"]
    assert [quick_replies(a) for a in asks(events)] == [[f"CANCEL:{oid}", f"KEEP:{oid}"]]


def said(w, text: str, **fields) -> dict:
    w.now += 50
    return make_event(
        "message_in",
        w.sid,
        w.now,
        "sim",
        thread_ref="th-synthetic-buyer",
        template=None,
        payload={"text": text, **fields},
    )


@pytest.mark.parametrize("text", ["có", "ok"])
async def test_bound_plain_yes_after_the_cancel_question_is_asked_again(world, monkeypatch, text):  # noqa: F811
    w = world
    await bound_order_then_cancel(w, monkeypatch, "complaint")
    yes = said(w, text)
    await w.ingest(yes)
    await w.service.expire_due(w.sid)
    await w.service.expire_due(w.sid)
    events = await w.events()
    assert [e["state"] for e in events if e["type"] == "order"] == ["DRAFT", "CONFIRM_SENT"]
    first, again = asks(events)
    assert quick_replies(again) == quick_replies(first) and yes["event_id"] in again["caused_by"]
    assert "receipt" not in [e["template"] for e in events if e["type"] == "message_out"]


async def test_bound_two_plain_yeses_in_one_pass_confirm_nothing(world, monkeypatch):  # noqa: F811
    w = world
    await bound_order_then_cancel(w, monkeypatch, "complaint")
    yeses = [said(w, "có"), said(w, "ok")]
    for yes in yeses:
        await w.ingest(yes)
    await w.service.expire_due(w.sid)
    await w.service.expire_due(w.sid)
    events = await w.events()
    assert [e["state"] for e in events if e["type"] == "order"] == ["DRAFT", "CONFIRM_SENT"]
    first, *again = asks(events)
    assert [yes["event_id"] in a["caused_by"] for a, yes in zip(again, yeses, strict=True)] == [True, True]
    assert "receipt" not in [e["template"] for e in events if e["type"] == "message_out"]


async def test_bound_plain_text_naming_the_order_then_a_plain_yes_is_asked_again(world, monkeypatch):  # noqa: F811
    w = world
    await bound_order_then_cancel(w, monkeypatch, "complaint")
    oid = next(e["order_id"] for e in await w.events() if e["type"] == "order")
    named, yes = said(w, "sao vậy", order_id=oid), said(w, "có")
    for message in (named, yes):
        await w.ingest(message)
        await w.service.expire_due(w.sid)
        await w.service.expire_due(w.sid)
    events = await w.events()
    assert [e["state"] for e in events if e["type"] == "order"] == ["DRAFT", "CONFIRM_SENT"]
    first, again = asks(events)
    assert quick_replies(again) == quick_replies(first) and yes["event_id"] in again["caused_by"]
    assert "receipt" not in [e["template"] for e in events if e["type"] == "message_out"]
