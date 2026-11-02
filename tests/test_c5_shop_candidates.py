"""The C5 shop on an order that waits for the seller to pick one frozen attachment candidate: it approves the one
candidate whose item the host held when the customer wrote (windows.json) and sends that card_id; it rejects the
order when no single candidate is that item. Without the card_id the app answers 422 and no order reaches CONFIRMED.
"""

import asyncio
import json

from app.settings import ROOT
from measure.c5_shop import Feed, Gold, Loops, Shop
from orders.flow import order_id_for

T0 = 1_000_000
GOLD = Gold(json.loads((ROOT / "data" / "comments" / "c5" / "windows.json").read_text()), {})
REASON = "seller must select one frozen attachment candidate; variant is unavailable or unresolved"


class Http:
    def __init__(self):
        self.posts = []

    async def post(self, url, json):
        self.posts.append((url, json))
        return type("Response", (), {"status_code": 200, "text": ""})()


def card(cid, version, frame, price):
    return {"type": "card", "event_id": f"{cid}-v{version}", "card_id": cid, "version": version,
            "ts_ms": T0 + frame + 1_000, "price": price, "provenance": {"frame_ts_ms": T0 + frame}}  # fmt: skip


def shop_with(comment_at_ms):
    """card-a opens on SP01 (offset 20 s), card-b on SP02 (offset 70 s); the customer writes at comment_at_ms."""
    events = [
        card("card-a", 1, 20_000, None),
        card("card-a", 2, 20_000, 250_000),
        card("card-b", 1, 70_000, None),
        card("card-b", 2, 70_000, 320_000),
        {"type": "comment", "event_id": "e-x", "comment_id": "cm-x", "ts_ms": T0 + comment_at_ms},
    ]
    feed = Feed()
    for seq, ev in enumerate(events, 1):
        feed.add(seq, ev)
    http = Http()
    shop = Shop(http=http, sid="s", gold=GOLD, feed=feed, loops=Loops([T0]), delay_s=0)
    order = {"type": "order", "event_id": "o-x.decide", "order_id": order_id_for("cm-x"), "state": "NEEDS_SELLER",
             "prev_state": "DRAFT", "reason": REASON, "ts_ms": T0 + comment_at_ms + 2_000,
             "lines": [{"card_id": "card-b", "card_version": 2, "candidates": ["card-b", "card-a"]}]}  # fmt: skip
    return shop, http, order


def test_the_shop_approves_the_candidate_the_host_held_and_sends_its_card_id():
    shop, http, order = shop_with(30_000)  # SP01 in the host's hands
    asyncio.run(shop.order(order))
    [(url, body)] = http.posts
    assert url.endswith(f"/orders/{order_id_for('cm-x')}/decision")
    assert (body["decision"], body.get("card_id")) == ("approve", "card-a")
    assert shop.log[0]["card_id"] == "card-a"


def test_the_shop_rejects_when_no_candidate_is_the_item_held():
    shop, http, order = shop_with(100_000)  # SP03 in the host's hands; neither card is SP03
    asyncio.run(shop.order(order))
    [(_, body)] = http.posts
    assert body == {"decision": "reject"}
