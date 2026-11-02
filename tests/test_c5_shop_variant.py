"""The C5 shop on a candidate approval the app refuses with 409 "variant is not unique on the selected card": the
decision request has no variant field (contracts/requests/order-decision.schema.json), so the shop rejects the order
and logs why, instead of leaving it waiting in NEEDS_SELLER with an unhandled 409."""

import asyncio
import json

from app.settings import ROOT
from measure.c5_shop import Feed, Gold, Loops, Shop
from orders.flow import order_id_for

T0 = 1_000_000
GOLD = Gold(json.loads((ROOT / "data" / "comments" / "c5" / "windows.json").read_text()), {})
REASON = "seller must select one frozen attachment candidate; variant is unavailable or unresolved"
REFUSAL = '{"detail":"variant is not unique on the selected card"}'


class Http:
    """Answers 409 to an approval and 200 to anything else, like the app on a card with several matching variants."""

    def __init__(self, approve_status=409, approve_text=REFUSAL):
        self.posts, self.approve = [], (approve_status, approve_text)

    async def post(self, url, json):
        self.posts.append((url, json))
        status, text = self.approve if json.get("decision") == "approve" else (200, "{}")
        return type("Response", (), {"status_code": status, "text": text})()


def shop_with(http):
    """card-a opens on SP01 (offset 20 s), card-b on SP02 (offset 70 s); the customer writes at 30 s (SP01 held)."""
    events = [
        {"type": "card", "event_id": "a-v1", "card_id": "card-a", "version": 1, "ts_ms": T0 + 21_000,
         "price": 250_000, "provenance": {"frame_ts_ms": T0 + 20_000}},
        {"type": "card", "event_id": "b-v1", "card_id": "card-b", "version": 1, "ts_ms": T0 + 71_000,
         "price": 320_000, "provenance": {"frame_ts_ms": T0 + 70_000}},
        {"type": "comment", "event_id": "e-x", "comment_id": "cm-x", "ts_ms": T0 + 30_000},
    ]  # fmt: skip
    feed = Feed()
    for seq, ev in enumerate(events, 1):
        feed.add(seq, ev)
    shop = Shop(http=http, sid="s", gold=GOLD, feed=feed, loops=Loops([T0]), delay_s=0)
    order = {"type": "order", "event_id": "o-x.decide", "order_id": order_id_for("cm-x"), "state": "NEEDS_SELLER",
             "prev_state": "DRAFT", "reason": REASON, "ts_ms": T0 + 32_000,
             "lines": [{"card_id": "card-b", "card_version": 1, "candidates": ["card-b", "card-a"]}]}  # fmt: skip
    return shop, order


def test_a_variant_409_on_the_approval_ends_as_a_logged_rejection():
    http = Http()
    shop, order = shop_with(http)
    asyncio.run(shop.order(order))
    assert [body for _, body in http.posts] == [
        {"decision": "approve", "card_id": "card-a"},
        {"decision": "reject"},
    ]
    [row] = shop.log
    assert (row["decision"], row["status"], row["card_id"]) == ("reject", 200, None)
    assert row["refused"] == {"decision": "approve", "card_id": "card-a", "status": 409, "response": REFUSAL}
    assert "409" in row["reason"] and "variant" in row["reason"]


def test_any_other_409_is_logged_as_is_and_not_turned_into_a_rejection():
    http = Http(approve_text='{"detail":"selected card has no current SKU and approved price"}')
    shop, order = shop_with(http)
    asyncio.run(shop.order(order))
    assert [body["decision"] for _, body in http.posts] == ["approve"]
    [row] = shop.log
    assert (row["decision"], row["status"], row["refused"], row["reason"]) == ("approve", 409, None, None)
