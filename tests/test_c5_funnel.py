"""measure/c5_score.funnel: the five C5 tiers counted apart over one n (every posted gold order comment), and the
chain of comments that pass every tier up to each one; receipts count version and image together."""

import json

import pytest

from app.settings import ROOT
from measure import c5_score
from measure.c5_score import Run
from measure.c5_shop import Gold
from orders.flow import order_id_for

T0 = 1_000_000
LOOPS = [{"k": 1, "t0_ms": T0, "end_ms": T0 + 351_500}]
GOLD = Gold(json.loads((ROOT / "data" / "comments" / "c5" / "windows.json").read_text()), {})
IMAGE = "data:image/png;base64,AAAA"


def card(cid, version, status, ts, frame=None, price=None):
    return {"type": "card", "event_id": f"{cid}-v{version}", "card_id": cid, "version": version, "status": status,
            "ts_ms": T0 + ts, "price": price, "sku": None, "caused_by": [], "image_url": IMAGE,
            "provenance": {} if frame is None else {"frame_ts_ms": T0 + frame}}  # fmt: skip


def comment(cid, ts):
    return {"type": "comment", "event_id": f"e-{cid}", "comment_id": cid, "ts_ms": T0 + ts}


def labeled(cid, ts, card_id):
    draft = {
        "sku": None,
        "variant": None,
        "qty": 1,
        "referenced_card_id": card_id,
        "reference_kind": "current_card",
    }
    return {"type": "comment_labeled", "event_id": f"l-{cid}", "comment_id": cid, "ts_ms": T0 + ts, "label": "order",
            "action": "none", "order_draft": draft}  # fmt: skip


def order(cid, ts, state, receipt=None, candidates=None):
    line = {"card_id": "card-a", "card_version": 2, "sku": None} | (
        {"candidates": candidates} if candidates else {}
    )
    return {"type": "order", "event_id": f"o-{cid}-{state}", "order_id": order_id_for(cid), "ts_ms": T0 + ts,
            "state": state, "prev_state": None, "actor": "system", "reason": None, "receipt": receipt,
            "lines": [line]}  # fmt: skip


def asked(cid, ts):
    return {"type": "message_out", "event_id": f"m-{cid}", "template": "confirm_order", "ts_ms": T0 + ts,
            "payload": {"order_id": order_id_for(cid), "card_id": "card-a", "card_version": 2}}  # fmt: skip


def row(cid, product):
    return {"comment_id": cid, "label": "order", "expected_action": "confirm_order",
            "order": {"product": product, "reference_kind": "current_card"}}  # fmt: skip


def confirmed(cid, ts, image):
    receipt = {"card_id": "card-a", "card_version": 2, "price": 250_000} | (
        {"image_url": IMAGE} if image else {}
    )
    return [order(cid, ts, "DRAFT"), asked(cid, ts + 500), order(cid, ts + 5_000, "CONFIRMED", receipt)]


def test_each_tier_is_counted_apart_and_the_chain_stops_at_the_first_miss():
    events = [card("card-a", 1, "draft", 16_500, 15_000), card("card-a", 2, "live", 26_000, price=250_000)] + [
        comment("c-ok", 30_000), labeled("c-ok", 31_000, "card-a"), *confirmed("c-ok", 31_500, image=True),
        comment("c-noimg", 32_000), labeled("c-noimg", 33_000, "card-a"), *confirmed("c-noimg", 33_500, image=False),
        comment("c-wrong", 34_000), labeled("c-wrong", 35_000, "card-a"), order("c-wrong", 35_500, "NEEDS_SELLER"),
        comment("c-quiet", 36_000),
        comment("c-pick", 37_000), labeled("c-pick", 38_000, "card-a"),
        order("c-pick", 38_500, "NEEDS_SELLER", candidates=["card-a", "card-b"]),
    ]  # fmt: skip
    rows = [row("c-ok", "SP01"), row("c-noimg", "SP01"), row("c-wrong", "SP02"), row("c-quiet", "SP01"),
            row("c-pick", "SP01")]  # fmt: skip
    run = Run(events, LOOPS, GOLD)
    rec = c5_score.receipts(run)
    out = c5_score.funnel(run, c5_score.order_ids(run, rows)["rows"], rec["rows"])
    assert out["n"] == 5
    # c-pick's draft card is the gold item, but the seller still has to pick between two frozen candidates
    assert out["tiers"] == {"intent": 4, "right_item": 2, "order": 4, "confirmed": 2, "receipt": 1}
    assert out["chain"] == {"intent": 4, "right_item": 2, "order": 2, "confirmed": 2, "receipt": 1}
    assert out["seller_pick"] == 1
    assert (rec["n"], rec["version_ok"], rec["with_image"], rec["version_and_image"]) == (2, 2, 1, 1)


def test_no_orders_leave_every_tier_after_intent_at_zero():
    events = [
        card("card-a", 1, "draft", 16_500, 15_000),
        comment("c-1", 30_000),
        labeled("c-1", 31_000, None),
    ]
    run = Run(events, LOOPS, GOLD)
    out = c5_score.funnel(run, c5_score.order_ids(run, [row("c-1", "SP01")])["rows"], [])
    assert out["tiers"] == {"intent": 1, "right_item": 0, "order": 0, "confirmed": 0, "receipt": 0}
    assert out["chain"]["receipt"] == 0


def test_a_gold_comment_with_no_item_is_never_the_right_item():
    """A card opened between items stands for no item; a gold row with no product must not match it as equal."""
    events = [
        card("card-z", 1, "draft", 6_000, 5_000),
        comment("c-1", 7_000),
        labeled("c-1", 8_000, "card-z"),
    ]
    run = Run(events, LOOPS, GOLD)
    out = c5_score.funnel(run, c5_score.order_ids(run, [row("c-1", None)])["rows"], [])
    assert out["tiers"]["right_item"] == 0


@pytest.mark.parametrize(
    "case", ["match", "wrong_card", "missing_card_image", "different_image", "wrong_version", "wrong_price"]
)
def test_receipt_tier_binds_the_quoted_card_version_price_and_image(case):
    cards = [card("card-a", 1, "draft", 16_500, 15_000), card("card-a", 2, "live", 26_000, price=250_000)]
    flow = confirmed("c-ok", 31_500, image=True)
    receipt = flow[-1]["receipt"]
    if case == "wrong_card":
        cards += [
            card("card-b", 1, "draft", 71_500, 70_000),
            card("card-b", 2, "live", 72_000, price=250_000),
        ]
        receipt["card_id"] = "card-b"
        flow[-1]["ts_ms"] = T0 + 90_000
    elif case == "missing_card_image":
        cards[1].pop("image_url")
    elif case == "different_image":
        cards[1]["image_url"] = "data:image/png;base64,QkJCQg=="
    elif case == "wrong_version":
        cards.append(card("card-a", 3, "live", 27_000, price=250_000))
        receipt["card_version"] = 3
    elif case == "wrong_price":
        receipt["price"] = 260_000
    run = Run(cards + [comment("c-ok", 30_000), labeled("c-ok", 31_000, "card-a"), *flow], LOOPS, GOLD)
    rec = c5_score.receipts(run)
    out = c5_score.funnel(run, c5_score.order_ids(run, [row("c-ok", "SP01")])["rows"], rec["rows"])
    expected = int(case == "match")
    assert rec["version_and_image"] == expected
    assert out["tiers"]["receipt"] == expected
    assert out["chain"]["receipt"] == expected
    assert out["chain"]["confirmed"] == 1
