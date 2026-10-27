"""D-024 and orders: a price_proposal changes no order price. A comment after a proposal the seller has not taken
is drafted at the card's price; a comment after the seller saves the price through the route is drafted at the new
price; a draft made before keeps the version and price it was drafted from. Synthetic events through the app's
ingest and price route; the order flow reads them as the orders worker does."""

from app.contracts import make_event, now_ms
from orders import flow
from tests.test_orders_ok_after_card_change import Recorder

SID = "pp-orders"
EVENTS = f"/api/sessions/{SID}/events"
SIZES = [{"size": "38", "color": "trắng", "stock": 3}]


def card(version: int, price: int, ts: int) -> dict:
    return make_event(
        "card", SID, ts, "replay", event_id=f"card-a.v{version}", card_id="card-a", version=version, sku="GIAY-01",
        name="Giày trắng", price=price, currency="VND", variants=SIZES, stock=3, image_url=None, status="live",
        provenance={"frame_ts_ms": None, "speech_event_ids": [], "edited_by": None},
    )  # fmt: skip


def commented(cid: str, ts: int) -> list[dict]:
    cm = make_event("comment", SID, ts, "replay", event_id=f"cm-{cid}", comment_id=cid, user_ref=f"u-{cid}",
                    text="chốt 38", reply_to=None)  # fmt: skip
    lb = make_event(
        "comment_labeled", SID, ts + 200, "replay", event_id=f"lb-{cid}", caused_by=[cm["event_id"]],
        comment_id=cid, label="order", scam_signals=[], action="none", latency_ms=200,
        order_draft={"sku": "GIAY-01", "variant": {"size": "38", "color": None}, "qty": 1,
                     "referenced_card_id": "card-a", "reference_kind": "current_card"},
    )  # fmt: skip
    return [cm, lb]


async def drafted(store, cid: str, now: int, before: list[dict]) -> list[dict]:
    """The orders worker's answer to comment `cid`, from the stored events plus the orders made before."""
    events = [*await store.read(SID, 0, 500), *before]
    [lb] = [e for e in events if e["event_id"] == f"lb-{cid}"]
    return await flow.on_labeled(lb, flow.View.of(events), Recorder(), now=now)


def pinned(out: list[dict]) -> tuple[str, int, int]:
    draft = out[0]
    line = draft["lines"][0]
    return draft["state"], line["card_version"], line["unit_price"]


async def test_a_proposal_changes_no_order_price_until_the_seller_saves_it(mem_app):
    _, store, client = mem_app
    t0 = now_ms() - 60_000
    proposal = make_event(
        "price_proposal", SID, t0 + 1000, "replay", event_id="pp-1", caused_by=["sp-1"], card_id="card-a",
        card_version=1, current_price=250000, heard_price=220000, speech_event_id="sp-1",
    )  # fmt: skip
    r = await client.post(EVENTS, json=[card(1, 250000, t0), proposal, *commented("c1", t0 + 2000)])
    assert r.status_code == 200

    # a comment after a proposal the seller has not taken: the card's price
    first = await drafted(store, "c1", t0 + 2500, [])
    assert pinned(first) == ("DRAFT", 1, 250000)
    assert first[1]["state"] == "CONFIRM_SENT" and first[1]["amount"] == 250000

    body = {"price": 220000, "card_version": 1, "proposal_event_id": "pp-1"}
    assert (await client.post(f"/api/sessions/{SID}/cards/card-a/price", json=body)).json() == {
        "changed": True,
        "version": 2,
    }
    seller_ts = (await store.card_versions(SID))[-1]["ts_ms"]

    # a comment after the seller saved: the new price
    assert (await client.post(EVENTS, json=commented("c2", seller_ts + 1000))).status_code == 200
    second = await drafted(store, "c2", seller_ts + 1500, first)
    assert pinned(second) == ("DRAFT", 2, 220000)
    assert second[1]["state"] == "CONFIRM_SENT" and second[1]["amount"] == 220000

    # the earlier draft keeps the version and price it was drafted from
    view = flow.View.of([*await store.read(SID, 0, 500), *first, *second])
    o1 = view.order(first[0]["order_id"])
    assert (o1["lines"][0]["card_version"], o1["lines"][0]["unit_price"], o1["amount"]) == (1, 250000, 250000)
