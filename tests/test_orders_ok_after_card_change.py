"""Case 10 of the C5 scenario: the customer's "ok" comes after the card changed. A new price, or the line's variant
no longer on the card, sends the order to the seller (NEEDS_SELLER) instead of CONFIRMED. The order keeps the
version and price it was sent with, and the event names the card version at the ok. A newer version that changes
neither (stock, name, another variant) is confirmed as before."""

import pytest

from orders import flow

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
    "variants": [{"size": "39", "color": "đen", "stock": 5}, {"size": "40", "color": "đen", "stock": 3}],
}
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


def card(version: int, ts: int, **change) -> dict:
    return {**CARD, "event_id": f"card-x{version}", "ts_ms": ts, "version": version, **change}


async def sent(*before: dict) -> list[dict]:
    """Comment at 2000 on v2; the confirmation (v2, 300.000đ) is sent at 3000, after any cards in `before`."""
    events = [CARD, COMMENT, LABEL, *before]
    out = await flow.on_labeled(LABEL, flow.View.of(events), Recorder(), now=3000)
    assert [e.get("state") or e["template"] for e in out] == ["DRAFT", "CONFIRM_SENT", "confirm_order"]
    return [*events, *out]


async def ok(events: list[dict], ts: int = 5000, now: int | None = None):
    mi = {
        "event_id": f"mi-{ts}",
        "type": "message_in",
        "ts_ms": ts,
        "thread_ref": "th-ux",
        "template": None,
        "payload": {"text": "ok"},
    }
    rec = Recorder()
    out = await flow.on_reply(mi, flow.View.of([*events, mi]), rec, now=ts if now is None else now)
    return out, [t for _, t, _ in rec.sent], [*events, mi, *out]


async def test_ok_on_the_version_that_was_sent_is_confirmed():
    events = await sent()
    out, templates, _ = await ok(events)
    assert [e.get("state") for e in out] == ["CONFIRMED", None] and templates == ["receipt"]
    assert out[0]["receipt"]["card_version"] == 2 and out[0]["receipt"]["price"] == 300000
    assert out[0]["caused_by"] == [events[-2]["event_id"]]


async def test_ok_after_a_version_with_the_same_price_and_variant_is_confirmed():
    events = await sent()
    v3 = card(3, 4000, name="Giày X mới", variants=[{"size": "39", "color": "đen", "stock": 1}])
    out, templates, _ = await ok([*events, v3])
    confirmed = out[0]
    assert confirmed["state"] == "CONFIRMED" and templates == ["receipt"]
    assert confirmed["receipt"]["card_version"] == 2 and confirmed["receipt"]["price"] == 300000
    assert confirmed["caused_by"] == [events[-2]["event_id"], "card-x3"]
    assert "v2" in confirmed["reason"] and "v3" in confirmed["reason"]


async def test_ok_after_a_price_change_goes_to_the_seller_with_the_sent_price():
    events = await sent()
    confirm_sent = events[-2]
    out, templates, _ = await ok([*events, card(3, 4000, price=250000)])
    held = out[0]
    assert (
        held["state"] == "NEEDS_SELLER" and held["prev_state"] == "CONFIRM_SENT" and held["approval_needed"]
    )
    assert held["lines"] == confirm_sent["lines"] and held["amount"] == 300000
    assert held["lines"][0]["card_version"] == 2 and held["receipt"] is None
    assert held["caused_by"] == [confirm_sent["event_id"], "card-x3"]
    assert "v2 300.000đ" in held["reason"] and "v3 250.000đ" in held["reason"]
    assert templates == ["waiting_for_shop"]


@pytest.mark.parametrize(
    "variants",
    [
        [{"size": "40", "color": "đen", "stock": 3}],
        [{"size": "39", "color": "trắng", "stock": 5}, {"size": "40", "color": "đen", "stock": 3}],
        [],
    ],
    ids=["size-gone", "color-gone", "no-variants"],
)
async def test_ok_after_the_line_variant_is_gone_goes_to_the_seller(variants):
    events = await sent()
    out, templates, _ = await ok([*events, card(3, 4000, variants=variants)])
    held = out[0]
    assert held["state"] == "NEEDS_SELLER" and held["lines"] == events[-2]["lines"]
    assert held["caused_by"] == [events[-2]["event_id"], "card-x3"]
    assert "size 39, đen" in held["reason"] and "v3" in held["reason"]
    assert templates == ["waiting_for_shop"]


async def test_a_change_written_after_the_ok_but_known_when_it_is_handled_counts():
    events = await sent()
    out, _, _ = await ok([*events, card(3, 5200, price=250000)], ts=5000, now=5500)
    assert out[0]["state"] == "NEEDS_SELLER" and "v3 250.000đ" in out[0]["reason"]


async def test_after_the_seller_approves_the_new_price_the_next_ok_is_confirmed():
    events = await sent()
    order_id = events[-2]["order_id"]
    out, _, events = await ok([*events, card(3, 4000, price=250000)])
    assert out[0]["state"] == "NEEDS_SELLER"
    approved = await flow.decide(
        flow.View.of(events), order_id, "approve", Recorder(), now=6000, unit_price=250000
    )
    assert approved[0]["state"] == "CONFIRM_SENT" and approved[0]["lines"][0]["unit_price"] == 250000
    events = [*events, *approved]

    out, templates, _ = await ok(events, ts=7000)
    assert out[0]["state"] == "CONFIRMED" and out[0]["amount"] == 250000 and templates == ["receipt"]

    out, templates, _ = await ok([*events, card(4, 6500, price=200000)], ts=7000)
    assert out[0]["state"] == "NEEDS_SELLER" and templates == ["waiting_for_shop"]
    assert "v3 250.000đ" in out[0]["reason"] and "v4 200.000đ" in out[0]["reason"]
    assert out[0]["lines"][0]["unit_price"] == 250000


async def test_a_change_before_the_send_keeps_the_comment_price_and_only_a_later_change_counts():
    events = await sent(card(3, 2500, price=280000))
    assert events[-2]["lines"][0]["card_version"] == 2 and events[-2]["amount"] == 300000
    out, templates, _ = await ok(events)
    assert out[0]["state"] == "CONFIRMED" and out[0]["amount"] == 300000 and templates == ["receipt"]

    out, templates, _ = await ok([*events, card(4, 4000, price=250000)])
    assert out[0]["state"] == "NEEDS_SELLER" and templates == ["waiting_for_shop"]
    assert "v3 280.000đ" in out[0]["reason"] and "v4 250.000đ" in out[0]["reason"]
    assert out[0]["lines"][0]["unit_price"] == 300000
