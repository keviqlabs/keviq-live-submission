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


def events(thread: str, quick_reply: str = "CONFIRM:o-c019", actor: str = "customer") -> list[dict]:
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
            "customer_ref": "u064",
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
