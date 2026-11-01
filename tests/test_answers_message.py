"""A price or stock question label becomes one private reply carrying the question's comment_id."""

import pytest

from answers import handlers, message
from answers.message import GUARD_MS, TEMPLATE, answer_id, on_question
from answers.reply import SELLER_CONFIRMS, Answer
from app.contracts import make_event
from connectors.sim.messenger import SimMessenger

T0 = 1_790_000_000_000
SID = "answers-test"
SHOES = [{"size": "38", "color": "đen", "stock": 2}, {"size": "39", "color": "đen", "stock": 3}]


def card(card_id="card-1", version=2, frame=T0, price=350000, status="live", sku="SP01", variants=None,
         name="SP01 giày đen"):  # fmt: skip
    variants = SHOES if variants is None else variants
    return {
        "event_id": f"{card_id}.v{version}", "session_id": SID, "ts_ms": frame + 500, "source": "replay",
        "v": 1, "type": "card", "card_id": card_id, "version": version, "sku": sku, "name": name,
        "price": price, "currency": "VND", "variants": variants, "stock": sum(v["stock"] for v in variants),
        "image_url": None, "status": status,
        "provenance": {"frame_ts_ms": frame, "speech_event_ids": [], "edited_by": None},
    }  # fmt: skip


def question(text, label="stock_question", cid="cm-1", ts=T0 + 10_000, user="u-1"):
    comment = {"event_id": f"ev.{cid}", "session_id": SID, "ts_ms": ts, "source": "replay", "v": 1,
               "type": "comment", "comment_id": cid, "user_ref": user, "text": text, "reply_to": None}  # fmt: skip
    labeled = {"event_id": f"lb.{cid}", "session_id": SID, "ts_ms": ts, "source": "replay", "v": 1,
               "type": "comment_labeled", "comment_id": cid, "label": label}  # fmt: skip
    return comment, labeled


async def ask(text, cards, label="stock_question", now=T0 + 11_000, extra=(), **kw):
    comment, labeled = question(text, label, **kw)
    return await on_question(labeled, [*cards, comment, labeled, *extra], SimMessenger(), now)


async def test_stock_question_gets_one_private_reply_with_its_comment_id():
    partials, reason = await ask("size 39 còn không shop", [card()])
    assert reason == "answer" and len(partials) == 1
    out = partials[0]
    assert out["type"] == "message_out" and out["template"] == TEMPLATE
    assert out["payload"]["comment_id"] == "cm-1"
    assert out["payload"]["send_api"]["recipient"] == {"comment_id": "cm-1"}
    assert out["payload"]["text"] == "Giày đen size 39 còn 3 ạ."
    assert (out["payload"]["card_id"], out["payload"]["card_version"]) == ("card-1", 2)
    assert out["thread_ref"] == "th-u-1" and out["ts_ms"] == T0 + 11_000
    assert out["event_id"] == answer_id("cm-1") and out["caused_by"] == ["lb.cm-1", "ev.cm-1", "card-1.v2"]


async def test_reply_is_a_valid_contract_event():
    (out,), _ = await ask("còn hàng không", [card()])
    fields = {k: v for k, v in out.items() if k not in ("type", "ts_ms")}
    event = make_event("message_out", SID, out["ts_ms"], "replay", **fields)
    assert event["payload"]["comment_id"] == "cm-1"
    long_id = answer_id("x" * 200)
    assert len(long_id) <= 96 and long_id.startswith("ans.h")


async def test_price_question_says_the_approved_price():
    (out,), reason = await ask("giá bao nhiêu vậy", [card()], label="price_question")
    assert reason == "answer" and out["payload"]["text"] == "Giày đen giá 350 nghìn đồng ạ."
    assert out["payload"]["kind"] == "price"


async def test_missing_stock_number_defers_to_the_seller():
    no_stock = card(sku=None, variants=[], name="bàn phím đen")
    (out,), reason = await ask("còn hàng không", [no_stock])
    assert reason == "defer" and out["payload"]["text"] == "Số lượng bàn phím đen " + SELLER_CONFIRMS
    assert "để người bán xác nhận" in out["payload"]["text"]


async def test_unapproved_price_defers_to_the_seller():
    draft = card(version=1, price=None, status="draft")
    (out,), reason = await ask("giá sao shop", [draft], label="price_question")
    assert reason == "defer" and out["payload"]["text"] == "Giá món này " + SELLER_CONFIRMS


async def test_latest_card_version_is_used():
    edited = card(version=3, price=320000, status="edited")
    (out,), _ = await ask("giá bao nhiêu", [card(), edited], label="price_question")
    assert out["payload"]["text"] == "Giày đen giá 320 nghìn đồng ạ."
    assert out["payload"]["card_version"] == 3 and out["caused_by"][-1] == "card-1.v3"


async def test_item_on_screen_when_the_comment_was_written():
    first, second = card(), card("card-2", frame=T0 + 20_000, name="SP02 áo trắng", price=150000)
    (out,), _ = await ask("giá nhiêu", [first, second], label="price_question", ts=T0 + 10_000)
    assert out["payload"]["card_id"] == "card-1"
    (out,), _ = await ask(
        "giá nhiêu", [first, second], label="price_question", ts=T0 + 30_000, now=T0 + 31_000
    )
    assert (
        out["payload"]["card_id"] == "card-2" and out["payload"]["text"] == "Áo trắng giá 150 nghìn đồng ạ."
    )


async def test_question_right_after_the_item_changed_stays_silent():
    first, second = card(), card("card-2", frame=T0 + 20_000, name="SP02 áo trắng")
    assert await ask("giá nhiêu", [first, second], "price_question", ts=T0 + 20_000 + GUARD_MS - 1) == (
        [],
        "just_switched",
    )
    partials, _ = await ask("giá nhiêu", [first, second], "price_question", ts=T0 + 20_000 + GUARD_MS)
    assert len(partials) == 1
    # the same card shown again is not a change
    again = card(version=3, frame=T0 + 20_000, status="edited")
    partials, _ = await ask("giá nhiêu", [card(), again], "price_question", ts=T0 + 20_500)
    assert len(partials) == 1


@pytest.mark.parametrize(
    ("cards", "text", "label", "reason"),
    [
        ([], "còn hàng không", "stock_question", "no_card"),
        ([card(status="retired", version=3)], "còn hàng không", "stock_question", "no_card"),
        ([card()], "cái vừa nãy còn không", "stock_question", "not_answered_here"),
        ([card()], "chốt 1 đôi", "order", "not_a_question"),
        ([card()], "chốt 1 đôi", "other", "not_a_question"),
    ],
)
async def test_silent_cases(cards, text, label, reason):
    assert await ask(text, cards, label) == ([], reason)


async def test_fixture_label_and_missing_comment_stay_silent():
    comment, labeled = question("còn hàng không")
    fixture = {**labeled, "event_id": "fx-label-1"}
    assert await on_question(fixture, [card(), comment, fixture], SimMessenger(), T0 + 11_000) == (
        [],
        "not_a_question",
    )
    assert await on_question(labeled, [card(), labeled], SimMessenger(), T0 + 11_000) == ([], "no_comment")


async def test_one_reply_per_comment():
    (out,), _ = await ask("còn hàng không", [card()])
    assert await ask("còn hàng không", [card()], extra=[out]) == ([], "already_answered")


async def test_closed_window_stays_silent():
    assert await ask("còn hàng không", [card()], now=T0 + 10_000 + 24 * 3600 * 1000 + 1) == ([], "no_window")


async def test_a_size_ask_is_left_to_the_order_flow():
    # an order without a size is the order flow's ask_variant; this module never asks
    comment, labeled = question("chốt 1 đôi", "order")
    assert await on_question(labeled, [card(), comment, labeled], SimMessenger(), T0 + 11_000) == (
        [],
        "not_a_question",
    )


async def test_recheck_drops_a_sentence_that_does_not_match_the_card(monkeypatch):
    def wrong(q, facts):
        return Answer(
            "answer", "Giày đen giá 999 nghìn đồng ạ.", facts.card_id, facts.version, q.kind, "price"
        )

    monkeypatch.setattr(message, "compose", wrong)
    assert await ask("giá bao nhiêu", [card()], label="price_question") == ([], "wrong_price")

    def old_version(q, facts):
        return Answer("answer", "Giày đen giá 350 nghìn đồng ạ.", facts.card_id, 1, q.kind, "price")

    monkeypatch.setattr(message, "compose", old_version)
    assert await ask("giá bao nhiêu", [card()], label="price_question") == ([], "version_changed")


class Runner:
    def __init__(self, bound):
        self.bound = bound

    async def attachment_enabled(self, sid):
        return self.bound


class Ctx:
    def __init__(self, bound):
        self.runner, self.received_ms = Runner(bound), T0 + 11_000


async def test_worker_handler_answers_unbound_sessions_only(monkeypatch):
    comment, labeled = question("size 38 còn không")
    loads = []

    async def load(ctx, sid):
        loads.append(sid)
        return [card(), comment, labeled]

    monkeypatch.setattr(handlers, "load_events", load)
    assert await handlers.on_labeled(labeled, Ctx(bound=True)) == []
    assert loads == []
    (out,) = await handlers.on_labeled(labeled, Ctx(bound=False))
    assert out["payload"]["comment_id"] == "cm-1" and out["payload"]["text"] == "Giày đen size 38 còn 2 ạ."
    assert await handlers.on_labeled({**labeled, "label": "order"}, Ctx(bound=False)) == []
    assert loads == [SID]


def test_worker_runs_the_handler():
    from worker.__main__ import handlers as merged

    assert handlers.on_labeled in merged()["comment_labeled"]
