"""measure/scenarios.py: the replayed events are contract events, and the invariant check fails on a wrong
customer (a metric that cannot fail measures nothing). The scenarios themselves run in `make integration`."""

import pytest

from app.contracts import validate_event
from measure import scenarios
from orders.flow import FIXTURE


@pytest.mark.parametrize(
    "build", [scenarios.labeled_session, scenarios.shield_session, scenarios.demo1_session]
)
def test_replayed_events_are_contract_events(build):
    for e in build():
        e.pop("_replay_at_ms", None)  # replay.plan takes it out before posting
        validate_event(e)


def test_cards_are_fixtures_only_where_labels_are_replayed():
    labeled, shield = scenarios.labeled_session(), scenarios.shield_session()
    assert all(FIXTURE.search(e["event_id"]) for e in labeled if e["type"] == "card")
    assert not any(FIXTURE.search(e["event_id"]) for e in labeled if e["type"] != "card")
    assert not any(FIXTURE.search(e["event_id"]) for e in shield)
    assert {e["type"] for e in shield} == {"card", "comment"}


def test_orders_session_holds_the_three_customers():
    evs = scenarios.labeled_session(scenarios.CUSTOMERS)
    cids = sorted(e["comment_id"] for e in evs if e["type"] == "comment")
    assert cids == ["c009", "c019", "c032", "c169", "c179", "c192"]


def events(
    thread: str, quick_reply: str = "CONFIRM:o-c019", actor: str = "customer", customer: str = "u064"
) -> list[dict]:
    return [
        {"event_id": "cm-c019", "type": "comment", "comment_id": "c019", "user_ref": "u064"},
        {
            "event_id": "mi-1",
            "type": "message_in",
            "thread_ref": thread,
            "payload": {"order_id": "o-c019", "quick_reply": quick_reply},
        },
        {
            "event_id": "o-c019-2",
            "type": "order",
            "order_id": "o-c019",
            "customer_ref": customer,
            "state": "CONFIRMED",
            "actor": actor,
            "caused_by": ["mi-1"],
        },
    ]


def test_invariants_accept_the_customers_own_confirm():
    assert scenarios.invariants(events("th-u064")) == []


@pytest.mark.parametrize(
    "evs",
    [
        events("th-u065"),  # another customer's tap
        events(
            "th-u065", customer="u065"
        ),  # o-c019 drafted for u065, who did not write c019, and confirmed by u065
        events("th-u064", quick_reply="CONFIRM:o-c009"),  # the customer's tap, for another order
        events("th-u064", actor="seller"),
        events("th-u064") + [{"event_id": "mc-1", "type": "model_call"}],
        events("th-u064")
        + [
            {
                "event_id": "mo-1",
                "type": "message_out",
                "thread_ref": "th-u065",
                "template": "receipt",
                "payload": {"order_id": "o-c019"},
            }
        ],
    ],
)
def test_invariants_flag_a_wrong_customer_or_a_model_call(evs):
    assert scenarios.invariants(evs)


def test_invariants_take_the_owner_from_the_comment_not_from_the_order():
    """o-c019 drafted for u065 (c019 was written by u064) and confirm_order sent to u065, no reply yet: the
    shape of the load session, where invariants are the only guard."""
    evs = [
        {"event_id": "cm-c019", "type": "comment", "comment_id": "c019", "user_ref": "u064"},
        {
            "event_id": "o-c019-1",
            "type": "order",
            "order_id": "o-c019",
            "customer_ref": "u065",
            "state": "DRAFT",
        },
        {
            "event_id": "mo-1",
            "type": "message_out",
            "thread_ref": "th-u065",
            "template": "confirm_order",
            "payload": {"order_id": "o-c019"},
        },
    ]
    bad = scenarios.invariants(evs)
    assert any("o-c019-1" in b for b in bad) and any("mo-1" in b for b in bad)


def test_changed_sees_an_order_event_that_the_old_size_check_missed():
    """Finding #3: with c019's size question open, u065's 'size 40 nha' drafting o-c032 passed the old check
    `not states(evs, "o-c019")`."""
    ask = {"event_id": "mo-1", "type": "message_out", "thread_ref": "th-u064", "template": "ask_variant"}
    before = [{"event_id": "cm-c019", "type": "comment", "comment_id": "c019", "user_ref": "u064"}, ask]
    reply = {
        "event_id": "mi-2",
        "type": "message_in",
        "thread_ref": "th-u065",
        "payload": {"text": "size 40 nha"},
    }
    order = {
        "event_id": "o-c032-1",
        "type": "order",
        "order_id": "o-c032",
        "state": "DRAFT",
        "caused_by": ["mi-9"],
    }
    after = before + [reply, order]
    assert not scenarios.states(after, "o-c019")  # the old check passes
    assert scenarios.changed(before, after, "mi-2") == ["o-c032-1"]
    assert scenarios.new_orders(before, after) == ["o-c032-1"]


def test_changed_sees_a_reply_and_nothing_when_nothing_happened():
    before = events("th-u064")
    reply = {
        "event_id": "mi-2",
        "type": "message_in",
        "thread_ref": "th-u065",
        "payload": {"text": "size 40 nha"},
    }
    out = {"event_id": "mo-2", "type": "message_out", "thread_ref": "th-u065", "caused_by": ["mi-2"]}
    assert scenarios.changed(before, before + [reply, out], "mi-2") == ["mo-2"]
    assert scenarios.changed(before, before + [reply], "mi-2") == []


def label(event_id: str, comment_id: str, signals: tuple[str, ...] = ()) -> dict:
    return {
        "event_id": event_id,
        "type": "comment_labeled",
        "comment_id": comment_id,
        "scam_signals": list(signals),
    }


def test_second_worker_flags_labels_that_the_replay_did_not_send():
    sid = "smoke-integ-1-orders"
    evs = [
        label(f"{sid}.gl-c009", "c009"),
        label(f"{sid}.gl-c169", "c169"),
        label("comment_labeled_ab12", "c169"),
    ]
    assert scenarios.second_worker(evs, sid, gold_only=True) == ["c169"]
    assert scenarios.second_worker(evs[:2], sid, gold_only=True) == []


def test_second_worker_allows_one_shield_and_one_batch_label():
    phone = [label("cl-1", "c003", ("phone",)), label("cl-2", "c003")]
    plain = [label("cl-3", "c004")]
    assert scenarios.second_worker(phone + plain, "s", gold_only=False) == []
    assert scenarios.second_worker(phone + plain + [label("cl-4", "c003")], "s", gold_only=False) == ["c003"]
    assert scenarios.second_worker(phone + plain + [label("cl-5", "c004")], "s", gold_only=False) == ["c004"]
