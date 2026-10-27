"""C6 invariants: the permission layer in orders/rules.py and the order state machine."""

import json
import re

import pytest

from app.settings import ROOT
from orders import rules
from orders.states import can_transition, machine


def test_rules_table_matches_the_contract():
    contract = json.loads((ROOT / "contracts/permissions.json").read_text())
    assert rules.PERMISSIONS == {k: v["decision"] for k, v in contract["actions"].items()}
    assert rules.SELLER_THRESHOLD_VND == contract["seller_threshold_vnd_default"]


@pytest.mark.parametrize("action", [a for a, d in rules.PERMISSIONS.items() if d == "forbidden"])
@pytest.mark.parametrize("actor", ["assistant", "seller", "customer", "system"])
def test_forbidden_actions_are_never_allowed(action, actor):
    d = rules.authorize(action, actor, approved_by_seller=True)
    assert not d.allowed and not d.needs_seller


@pytest.mark.parametrize("action", [a for a, d in rules.PERMISSIONS.items() if d == "seller"])
def test_discount_refund_and_threshold_need_the_seller(action):
    for actor in ("assistant", "customer", "system"):
        d = rules.authorize(action, actor)
        assert not d.allowed and d.needs_seller
    assert rules.authorize(action, "seller").allowed
    assert rules.authorize(action, "assistant", approved_by_seller=True).allowed


def test_unknown_action_is_denied():
    assert not rules.authorize("transfer_money").allowed


def test_draft_checks_send_discounts_and_mismatches_to_the_seller():
    ok = rules.evaluate_draft(450000, 450000, 2, 2)
    assert ok.allowed
    for d in (
        rules.evaluate_draft(400000, 450000, 2, 2),  # price taken from the customer's words
        rules.evaluate_draft(450000, 450000, 2, 2, discount_requested=True),
        rules.evaluate_draft(450000, 450000, 3, 2),  # over stock
        rules.evaluate_draft(690000, 690000, 3, 4),  # 2 070 000 > threshold
        rules.evaluate_draft(None, None, 1, 1),  # card has no spoken price yet
    ):
        assert not d.allowed and d.needs_seller and d.reason


def test_no_message_outside_24h():
    h = 3600 * 1000
    assert rules.can_message(0, 23 * h).allowed
    assert not rules.can_message(0, 24 * h + 1).allowed
    assert not rules.can_message(None, 0).allowed


def test_confirmed_only_by_the_customer():
    assert can_transition("CONFIRM_SENT", "CONFIRMED", "customer")[0]
    for actor in ("assistant", "seller", "system"):
        assert not can_transition("CONFIRM_SENT", "CONFIRMED", actor)[0]
    assert not can_transition("DRAFT", "CONFIRMED", "customer")[0]
    assert not can_transition("NEEDS_SELLER", "CONFIRMED", "seller", customer_confirmed=False)[0]
    assert can_transition("NEEDS_SELLER", "CONFIRMED", "seller", customer_confirmed=True)[0]


def test_only_the_seller_leaves_needs_seller_except_customer_cancel():
    for to in ("DRAFT", "CONFIRM_SENT"):
        assert can_transition("NEEDS_SELLER", to, "seller")[0]
        assert not can_transition("NEEDS_SELLER", to, "assistant")[0]
    assert can_transition("NEEDS_SELLER", "CANCELLED", "customer")[0]
    assert not can_transition("NEEDS_SELLER", "CANCELLED", "assistant")[0]


def test_terminal_states_have_no_exit_and_new_orders_start_in_draft():
    for s in machine()["terminal"]:
        for to in machine()["states"]:
            assert not can_transition(s, to, "seller")[0]
    assert can_transition(None, "DRAFT", "assistant")[0]
    assert not can_transition(None, "CONFIRMED", "customer")[0]


def test_demo_orders_follow_the_state_machine(demo_events):
    confirmed_by_customer: set[str] = set()
    last: dict[str, str | None] = {}
    for e in sorted((e for e in demo_events if e["type"] == "order"), key=lambda e: e["ts_ms"]):
        assert e["prev_state"] == last.get(e["order_id"])
        ok, why = can_transition(
            e["prev_state"], e["state"], e["actor"], e["order_id"] in confirmed_by_customer
        )
        assert ok, (e["event_id"], why)
        if e["state"] == "CONFIRMED":
            confirmed_by_customer.add(e["order_id"])
        last[e["order_id"]] = e["state"]


PAYMENT = re.compile(r"collect_payment|charge\(|capture_payment|payout|transfer_funds|promise_delivery", re.I)


def test_no_code_path_collects_money_or_promises_delivery():
    hits = []
    for d in ("app", "orders", "worker", "connectors", "meta", "measure"):
        for f in (ROOT / d).rglob("*.py"):
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if PAYMENT.search(line) and not (f.name == "rules.py" and '"forbidden"' in line):
                    hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, hits
