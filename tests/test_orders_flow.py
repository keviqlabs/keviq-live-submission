"""orders/: draft from the card version live at the comment, rules decide, the seller decides in one tap, the
customer confirms in Messenger (sim), the receipt carries that version's image and price (C5), every step is
traceable to its comment, card version and actor (C6)."""

import json

import pytest

from app.contracts import now_ms
from connectors.sim import replay
from connectors.sim.messenger import webhook_body
from measure.core import c1, c5
from orders import flow, handlers
from orders.states import can_transition
from worker.runner import Runner

SID = "t1"


def without_fixture_orders(demo_events: list[dict]) -> list[dict]:
    """demo1 with the recorded orders and messages removed and the other fixtures kept as real events."""
    out = []
    for src in demo_events:
        if src["event_id"].startswith(("fx-o", "fx-msg")):
            continue
        e = json.loads(json.dumps(src))
        e["event_id"] = e["event_id"].removeprefix("fx-")
        if "caused_by" in e:
            e["caused_by"] = [c.removeprefix("fx-") for c in e["caused_by"]]
        out.append(e)
    return out


async def play(client, events: list[dict], target: str = SID) -> tuple[Runner, list[dict]]:
    """Post each event in arrival order and run the orders handlers on it, as the worker would."""
    runner = Runner("http://test", handlers.HANDLERS, http=client)
    produced = []
    for _, e in replay.plan(events, target, start_ms=now_ms() - 120_000):
        assert (await client.post(f"/api/sessions/{target}/events", json=[e])).status_code == 200
        produced += await runner.handle(e)
    assert runner.failures == 0
    return runner, produced


def orders(events: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in events:
        if e["type"] == "order":
            out.setdefault(e["order_id"], []).append(e)
    return out


def messages(events: list[dict], order_id: str) -> list[dict]:
    return [e for e in events if e["type"] == "message_out" and e["payload"].get("order_id") == order_id]


async def stored(store) -> list[dict]:
    return await store.read(SID, 0, 1000)


@pytest.fixture
async def played(mem_app, demo_events):
    _, store, client = mem_app
    runner, _ = await play(client, without_fixture_orders(demo_events))
    return runner, store, client


async def test_drafts_follow_the_rules_and_the_card_version_at_the_comment(played):
    _, store, _ = played
    events = await stored(store)
    o = orders(events)
    assert sorted(o) == ["o-05", "o-06", "o-07", "o-08", "o-11", "o-14"]  # cm-09 is unresolved: no order
    states = {oid: [e["state"] for e in h] for oid, h in o.items()}
    assert states["o-05"] == ["DRAFT", "NEEDS_SELLER"]
    assert states["o-06"] == ["DRAFT", "CONFIRM_SENT", "CONFIRMED"]  # mi-06
    assert states["o-07"] == ["DRAFT", "CONFIRM_SENT"]  # late comment, still priced from card-a v2
    assert states["o-11"] == ["DRAFT", "CONFIRM_SENT", "CONFIRMED"]  # mi-11
    assert states["o-14"] == ["DRAFT", "NEEDS_SELLER"]
    assert "qty 3 > stock 2" in o["o-14"][-1]["reason"]  # 4 red bags, 2 already held by o-06 and o-08

    [line] = o["o-05"][0]["lines"]
    assert line == {
        "sku": "GIAY-01",
        "card_id": "card-a",
        "card_version": 2,
        "variant": {"size": "39", "color": "trắng"},
        "qty": 2,
        "unit_price": 450000,
    }
    assert "discount" in o["o-05"][-1]["reason"] and "giảm còn 400" in o["o-05"][-1]["reason"]
    assert o["o-05"][-1]["approval_needed"] is True
    assert o["o-07"][0]["lines"][0]["card_version"] == 2


async def test_o05_waits_for_the_seller_and_gets_no_confirmation(played):
    _, store, _ = played
    msgs = messages(await stored(store), "o-05")
    assert [m["template"] for m in msgs] == ["waiting_for_shop"]
    assert msgs[0]["payload"]["send_api"]["recipient"] == {"comment_id": "cm-05"}  # private reply


async def test_confirmation_carries_what_the_customer_agrees_to(played):
    _, store, _ = played
    [m] = messages(await stored(store), "o-07")
    p = m["payload"]
    assert m["template"] == "confirm_order" and m["thread_ref"] == "th-u07"
    for key in ("size", "color", "qty", "unit_price", "amount", "address", "payment_method"):
        assert key in p
    assert (p["size"], p["color"], p["qty"], p["unit_price"], p["amount"]) == (
        "40",
        "trắng",
        1,
        450000,
        450000,
    )
    assert p["payment_method"] == "cod"
    body = p["send_api"]
    assert body["recipient"] == {"comment_id": "cm-07"} and body["messaging_type"] == "RESPONSE"
    assert [q["payload"] for q in body["message"]["quick_replies"]] == ["CONFIRM:o-07", "CANCEL:o-07"]


async def test_receipts_have_the_version_image_and_price(played):
    _, store, _ = played
    events = await stored(store)
    got = c5(events, {"comments": {}})
    assert (got["receipts"], got["receipts_ok"]) == (2, 2)
    [receipt] = [m for m in messages(events, "o-06") if m["template"] == "receipt"]
    assert receipt["payload"]["send_api"]["message"]["attachment"]["payload"]["elements"][0]["image_url"]


async def test_every_order_label_has_a_consequence_within_one_cycle(played):
    _, store, _ = played
    events = await stored(store)
    report = c1([e for e in events if e["type"] != "comment_labeled" or e["label"] == "order"])
    assert report["missing"] == [f"{SID}.lb-09"]  # unresolved reference: the comments stream asks which item


async def test_transitions_and_permissions_hold_on_every_order(played):
    _, store, _ = played
    for history in orders(await stored(store)).values():
        prev = None
        for e in history:
            ok, why = can_transition(prev and prev["state"], e["state"], e["actor"])
            assert ok, why
            assert e["state"] != "CONFIRMED" or e["actor"] == "customer"
            if prev and e["lines"][0]["unit_price"] != prev["lines"][0]["unit_price"]:
                assert e["actor"] == "seller"
            prev = e


async def test_scene_o05_seller_approves_400k_then_customer_confirms(played):
    runner, store, client = played
    r = await client.post(
        f"/api/sessions/{SID}/orders/o-05/decision", json={"decision": "approve", "unit_price": 400000}
    )
    assert r.status_code == 200, r.text
    order, msg = r.json()["events"]
    assert (order["state"], order["actor"], order["amount"]) == ("CONFIRM_SENT", "seller", 800000)
    assert order["lines"][0]["unit_price"] == 400000 and "400000" in order["reason"]
    assert order["caused_by"][0].startswith(f"{SID}.") and order["caused_by"][0] in {
        e["event_id"] for e in orders(await stored(store))["o-05"]
    }
    assert msg["template"] == "confirm_order" and msg["payload"]["amount"] == 800000
    assert msg["payload"]["card_price"] == 450000
    assert msg["payload"]["send_api"]["recipient"] == {"id": "u05"}  # not a second private reply

    body = webhook_body("th-u05", now_ms(), text="Đồng ý", quick_reply="CONFIRM:o-05")
    r = await client.post(f"/api/sessions/{SID}/sim/messenger", json=body)
    assert r.status_code == 200, r.text
    [mi] = r.json()["events"]
    assert (mi["source"], mi["template"], mi["payload"]["order_id"]) == ("sim", "confirm_yes", "o-05")
    out = await runner.handle(mi)
    confirmed, receipt = out
    assert (confirmed["state"], confirmed["actor"]) == ("CONFIRMED", "customer")
    assert confirmed["receipt"] == {
        "card_id": "card-a",
        "card_version": 2,
        "image_url": "sim://sam/card-a-v2.png",
        "price": 450000,
    }
    assert receipt["template"] == "receipt" and receipt["payload"]["amount"] == 800000

    log = (await client.get(f"/api/sessions/{SID}/orders/o-05/log")).json()
    assert log["comment"]["text"].startswith("Lấy đôi vừa rồi, size 39")
    assert (log["card"]["card_id"], log["card"]["version"], log["card"]["price"]) == ("card-a", 2, 450000)
    assert log["label"]["event_id"] == f"{SID}.lb-05"
    assert [s["actor"] for s in log["steps"]] == ["assistant", "assistant", "seller", "customer"]
    assert log["discount_request"]["value"] == 400000
    assert [m["template"] for m in log["messages"] if m["type"] == "message_out"] == [
        "waiting_for_shop",
        "confirm_order",
        "receipt",
    ]


async def test_seller_keeps_the_card_price_or_rejects(played):
    _, store, client = played
    r = await client.post(f"/api/sessions/{SID}/orders/o-05/decision", json={"decision": "approve"})
    assert r.json()["events"][0]["amount"] == 900000
    r = await client.post(f"/api/sessions/{SID}/orders/o-14/decision", json={"decision": "reject"})
    order = r.json()["events"][0]
    assert (order["state"], order["actor"]) == ("CANCELLED", "seller")


async def test_decision_refused_outside_needs_seller(played):
    _, _, client = played
    r = await client.post(f"/api/sessions/{SID}/orders/o-06/decision", json={"decision": "approve"})
    assert r.status_code == 409
    r = await client.post(f"/api/sessions/{SID}/orders/o-99/decision", json={"decision": "approve"})
    assert r.status_code == 404
    r = await client.post(
        f"/api/sessions/{SID}/orders/o-05/decision", json={"decision": "approve", "unit_price": -1}
    )
    assert r.status_code == 409


async def test_another_thread_cannot_confirm_an_order(played):
    runner, store, client = played
    body = webhook_body("th-u99", now_ms(), quick_reply="CONFIRM:o-07")
    [mi] = (await client.post(f"/api/sessions/{SID}/sim/messenger", json=body)).json()["events"]
    assert await runner.handle(mi) == []
    assert orders(await stored(store))["o-07"][-1]["state"] == "CONFIRM_SENT"


async def test_customer_can_cancel(played):
    runner, store, client = played
    body = webhook_body("th-u07", now_ms(), quick_reply="CANCEL:o-07")
    [mi] = (await client.post(f"/api/sessions/{SID}/sim/messenger", json=body)).json()["events"]
    order, _ = await runner.handle(mi)
    assert (order["state"], order["actor"]) == ("CANCELLED", "customer")


async def test_handlers_are_idempotent(played, demo_events):
    runner, store, _ = played
    before = len(await stored(store))
    label = next(
        e
        for _, e in replay.plan(without_fixture_orders(demo_events), SID, 0)
        if e["event_id"].endswith("lb-05")
    )
    assert await runner.handle(label) == []
    assert len(await stored(store)) == before


async def test_fixture_replay_is_left_alone(mem_app, demo_events):
    _, _, client = mem_app
    _, produced = await play(client, demo_events, "fx1")
    assert produced == []


async def test_sim_messenger_refused_in_a_facebook_session(mem_app, demo_events):
    _, _, client = mem_app
    comment = {
        **next(e for e in demo_events if e["type"] == "comment"),
        "session_id": "live1",
        "source": "facebook",
    }
    assert (await client.post("/api/sessions/live1/events", json=[comment])).status_code == 200
    r = await client.post(
        "/api/sessions/live1/sim/messenger", json=webhook_body("th-u01", now_ms(), text="ok")
    )
    assert r.status_code == 403


class Recorder:
    def __init__(self):
        self.sent = []

    async def send(self, thread_ref, template, payload):
        self.sent.append((thread_ref, template, payload))
        return {"type": "message_out", "thread_ref": thread_ref, "template": template, "payload": payload}


def scene(comment_ts: int, card_status: str = "live") -> tuple[dict, flow.View]:
    card = {
        "event_id": "card-x2",
        "type": "card",
        "ts_ms": 1000,
        "card_id": "card-x",
        "version": 2,
        "status": card_status,
        "sku": "X-1",
        "name": "Giày X",
        "price": 300000,
        "image_url": "sim://x.png",
        "variants": [{"size": "39", "color": "đen", "stock": 5}],
    }
    comment = {
        "event_id": "cm-x",
        "type": "comment",
        "ts_ms": comment_ts,
        "comment_id": "cm-x",
        "user_ref": "ux",
        "text": "chốt 1",
    }
    label = {
        "event_id": "lb-x",
        "type": "comment_labeled",
        "ts_ms": comment_ts,
        "comment_id": "cm-x",
        "label": "order",
        "order_draft": {"variant": {"size": "39", "color": None}, "qty": 1, "referenced_card_id": "card-x"},
    }
    return label, flow.View.of([card, comment, label])


async def test_clean_draft_is_sent_for_confirmation():
    label, view = scene(comment_ts=2000)
    out = await flow.on_labeled(label, view, Recorder(), now=3000)
    assert [e.get("state") or e["template"] for e in out] == ["DRAFT", "CONFIRM_SENT", "confirm_order"]
    assert out[0]["lines"][0]["variant"] == {"size": "39", "color": "đen"}
    assert out[0]["caused_by"] == ["cm-x", "card-x2"]


async def test_no_message_outside_the_24h_window():
    label, view = scene(comment_ts=2000)
    rec = Recorder()
    out = await flow.on_labeled(label, view, rec, now=2000 + 25 * 3600 * 1000)
    assert [e.get("state") for e in out] == ["DRAFT", "NEEDS_SELLER"]
    assert "24h" in out[1]["reason"] and rec.sent == []


async def test_a_card_that_was_not_live_needs_the_seller():
    label, view = scene(comment_ts=2000, card_status="draft")
    out = await flow.on_labeled(label, view, Recorder(), now=3000)
    assert out[1]["state"] == "NEEDS_SELLER" and "was draft" in out[1]["reason"]
