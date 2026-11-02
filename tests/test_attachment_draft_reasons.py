"""The NEEDS_SELLER reason of an order drafted from an attachment (app.attachment_orders.draft_attachment).

The rules' refusal is shown next to the blocking reasons only when it is about the card the order is on: a variant
missing from the card is bounded by the card's stock, and while the seller still picks among frozen candidates the
first candidate's stock is not shown (the seller's choice is checked again)."""

import pytest

from app import attachment_orders


def card(card_id: str, variants: list[dict], stock: int) -> dict:
    return {
        "type": "card",
        "event_id": f"{card_id}-v1",
        "card_id": card_id,
        "version": 1,
        "ts_ms": 1000,
        "status": "live",
        "sku": f"{card_id}-sku",
        "name": card_id,
        "price": 300000,
        "currency": "VND",
        "image_url": None,
        "variants": variants,
        "stock": stock,
        "provenance": {},
    }


A = card(
    "card-a", [{"size": "39", "color": "đen", "stock": 5}, {"size": "40", "color": "đen", "stock": 3}], 8
)
B = card("card-b", [{"size": "41", "color": "đen", "stock": 4}], 4)
COMMENT = {
    "type": "comment",
    "event_id": "cm-1",
    "comment_id": "cm-1",
    "ts_ms": 2000,
    "user_ref": "u1",
    "text": "chốt 41",
    "session_id": "s",
    "source": "sim",
}


def label(size: str | None, qty: int = 1) -> dict:
    return {
        "type": "comment_labeled",
        "event_id": "lb-1",
        "comment_id": "cm-1",
        "ts_ms": 2000,
        "label": "order",
        "order_draft": {"variant": {"size": size, "color": None}, "qty": qty, "referenced_card_id": None},
    }


class Store:
    async def append_order_batch(self, out, intent=None):
        return None


async def reason(cards: list[dict], lab: dict, candidates: list[str] | None) -> tuple[str, str]:
    out = await attachment_orders.draft_attachment(
        Store(),
        {"event_id": "att-1"},
        [*cards, COMMENT, lab],
        COMMENT,
        lab,
        cards[0],
        3000,
        candidates,
        persist=not candidates,
    )
    order = [e for e in out if e["type"] == "order"][-1]
    return order["state"], order["reason"]


@pytest.mark.parametrize(
    ("cards", "lab", "candidates", "want"),
    [
        ([A], label("41"), None, "variant is unavailable or unresolved on the selected card"),
        (
            [A, B],
            label("41"),
            ["card-a", "card-b"],
            "seller must select one frozen attachment candidate; "
            "variant is unavailable or unresolved on the selected card",
        ),
        (
            [card("card-a", [], 0), card("card-b", [], 5)],
            label(None),
            ["card-a", "card-b"],
            "seller must select one frozen attachment candidate",
        ),
    ],
    ids=["size-not-on-card", "candidates-size-on-second", "candidates-first-sold-out"],
)
async def test_no_stock_refusal_that_is_not_about_the_ordered_card(cards, lab, candidates, want):
    assert await reason(cards, lab, candidates) == ("NEEDS_SELLER", want)


async def test_a_missing_variant_above_the_card_stock_shows_the_stock_refusal_too():
    assert await reason([A], label("41", qty=9), None) == (
        "NEEDS_SELLER",
        "variant is unavailable or unresolved on the selected card; qty 9 > stock 8",
    )
