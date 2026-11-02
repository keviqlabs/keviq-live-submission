"""The card version an order is locked to, and what the quote and the receipt say about it.

At drafting the order takes the version current at the comment, or a later version known at drafting that changes
only the image or the name (same SKU, price, currency, variants, stock, status). Any other change keeps the
comment-time version, as before. A locked version without a SKU goes to the seller. After the quote, a new SKU goes
to the seller like a new price or a dropped variant; a new image or name does not. The quote carries the locked
version's image, the receipt carries its SKU and name. measure/c5_score.receipts counts the receipt image."""

import json

import pytest

from app.settings import ROOT
from measure import c5_score
from measure.c5_score import Run
from measure.c5_shop import Gold
from orders import flow

V1 = {
    "event_id": "card-x1",
    "type": "card",
    "ts_ms": 1000,
    "card_id": "card-x",
    "version": 1,
    "status": "live",
    "sku": "X-1",
    "name": "Giày X",
    "price": 300000,
    "currency": "VND",
    "image_url": None,
    "variants": [{"size": "39", "color": "đen", "stock": 5}, {"size": "40", "color": "đen", "stock": 3}],
    "provenance": {},
}
IMAGE = "sim://x.png"
COMMENT = {
    "event_id": "cm-x",
    "type": "comment",
    "ts_ms": 2000,
    "comment_id": "cm-x",
    "user_ref": "ux",
    "text": "chốt 39",
}
LABEL = {
    "event_id": "lb-x",
    "type": "comment_labeled",
    "ts_ms": 2000,
    "comment_id": "cm-x",
    "label": "order",
    "order_draft": {"variant": {"size": "39", "color": None}, "qty": 1, "referenced_card_id": "card-x"},
}


class Recorder:
    def __init__(self):
        self.sent = []

    async def send(self, thread_ref, template, payload):
        self.sent.append((thread_ref, template, payload))
        return {"type": "message_out", "thread_ref": thread_ref, "template": template, "payload": payload}


def card(version: int, ts: int, base: dict = V1, **change) -> dict:
    return {**base, "event_id": f"card-x{version}", "ts_ms": ts, "version": version, **change}


async def drafted(first: dict = V1, *before: dict) -> tuple[list[dict], list[dict]]:
    """Comment at 2000 on `first`; the draft is made at 3000, after the cards in `before`."""
    events = [first, COMMENT, LABEL, *before]
    out = await flow.on_labeled(LABEL, flow.View.of(events), Recorder(), now=3000)
    return [*events, *out], out


async def ok(events: list[dict], ts: int = 5000) -> tuple[list[dict], list[dict]]:
    mi = {
        "event_id": f"mi-{ts}",
        "type": "message_in",
        "ts_ms": ts,
        "thread_ref": "th-ux",
        "template": None,
        "payload": {"text": "ok"},
    }
    out = await flow.on_reply(mi, flow.View.of([*events, mi]), Recorder(), now=ts)
    return [*events, mi, *out], out


def states(out: list[dict]) -> list[str]:
    return [e.get("state") or e["template"] for e in out]


@pytest.mark.parametrize(
    ("later", "locked", "name"),
    [
        ([card(2, 2500, image_url=IMAGE)], 2, "Giày X"),
        ([card(2, 2500, image_url=IMAGE, name="Giày X đen")], 2, "Giày X đen"),
        (
            [card(2, 2500, image_url=IMAGE), card(3, 2700, image_url=IMAGE, name="Giày X đen")],
            3,
            "Giày X đen",
        ),
    ],
    ids=["image", "image-and-name", "image-then-name"],
)
async def test_a_version_that_only_adds_the_image_before_drafting_is_quoted_and_receipted(
    later, locked, name
):
    events, out = await drafted(V1, *later)
    assert states(out) == ["DRAFT", "CONFIRM_SENT", "confirm_order"]
    draft, quote = out[0], out[2]["payload"]
    assert draft["lines"][0]["card_version"] == locked and draft["amount"] == 300000
    assert f"card-x{locked}" in draft["caused_by"] and f"v{locked}" in draft["reason"]
    assert quote["card_version"] == locked and quote["image_url"] == IMAGE and quote["name"] == name

    events, out = await ok(events)
    assert states(out) == ["CONFIRMED", "receipt"]
    receipt, msg = out[0]["receipt"], out[1]["payload"]
    assert receipt == {"card_id": "card-x", "card_version": locked, "image_url": IMAGE, "price": 300000}
    assert msg["card_version"] == locked and msg["image_url"] == IMAGE and msg["name"] == name


async def test_a_card_without_a_sku_goes_to_the_seller():
    _, out = await drafted(card(1, 1000, sku=None, image_url=IMAGE))
    assert states(out) == ["DRAFT", "NEEDS_SELLER", "waiting_for_shop"]
    assert out[1]["approval_needed"] and "no SKU" in out[1]["reason"]


async def test_ok_after_a_version_that_only_changes_the_sku_goes_to_the_seller():
    events, out = await drafted(card(1, 1000, image_url=IMAGE))
    assert states(out) == ["DRAFT", "CONFIRM_SENT", "confirm_order"]
    events, out = await ok([*events, card(2, 4000, image_url=IMAGE, sku="X-2")])
    assert states(out) == ["NEEDS_SELLER", "waiting_for_shop"]
    assert out[0]["lines"][0]["card_version"] == 1 and out[0]["receipt"] is None
    assert "X-1" in out[0]["reason"] and "X-2" in out[0]["reason"] and "v2" in out[0]["reason"]


async def test_the_quote_carries_the_image_and_the_receipt_carries_sku_and_name():
    events, out = await drafted(card(1, 1000, image_url=IMAGE))
    quote = out[2]["payload"]
    assert quote["image_url"] == IMAGE and quote["sku"] == "X-1" and quote["name"] == "Giày X"
    _, out = await ok(events)
    msg = out[1]["payload"]
    assert msg["sku"] == "X-1" and msg["name"] == "Giày X" and msg["image_url"] == IMAGE


@pytest.mark.parametrize(
    "later",
    [
        card(2, 2500, image_url=IMAGE, price=280000),
        card(2, 2500, image_url=IMAGE, sku="X-2"),
        card(2, 2500, image_url=IMAGE, variants=[{"size": "39", "color": "đen", "stock": 1}]),
        card(2, 2500, image_url=IMAGE, status="retired"),
    ],
    ids=["price", "sku", "stock", "retired"],
)
async def test_a_version_that_changes_more_than_the_image_before_drafting_keeps_the_comment_version(later):
    """Pins today's behaviour: the order stays on v1 at v1's price, so the receipt has no image."""
    events, out = await drafted(V1, later)
    assert states(out) == ["DRAFT", "CONFIRM_SENT", "confirm_order"]
    assert out[0]["lines"][0]["card_version"] == 1 and out[0]["amount"] == 300000
    assert out[2]["payload"]["card_version"] == 1 and out[2]["payload"].get("image_url") is None
    events, out = await ok(events)
    assert states(out) == ["CONFIRMED", "receipt"]
    assert out[0]["receipt"] == {"card_id": "card-x", "card_version": 1, "image_url": None, "price": 300000}


async def test_an_image_only_version_after_the_quote_is_confirmed_on_the_quoted_version():
    """Pins today's behaviour: the image came too late for the quote, so the receipt keeps the quoted version."""
    events, out = await drafted()
    assert states(out) == ["DRAFT", "CONFIRM_SENT", "confirm_order"]
    events, out = await ok([*events, card(2, 4000, image_url=IMAGE)])
    assert states(out) == ["CONFIRMED", "receipt"]
    assert out[0]["receipt"] == {"card_id": "card-x", "card_version": 1, "image_url": None, "price": 300000}
    assert out[1]["payload"]["image_url"] is None


async def test_c5_receipts_count_the_image_of_the_locked_version():
    events, _ = await drafted(V1, card(2, 2500, image_url=IMAGE))
    events, _ = await ok(events)
    gold = Gold(json.loads((ROOT / "data" / "comments" / "c5" / "windows.json").read_text()), {})
    got = c5_score.receipts(Run(events, [], gold))
    assert (got["n"], got["version_ok"], got["with_image"]) == (1, 1, 1)
