"""Structural seller-choice contracts; history and permission checks belong to the route implementation."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.contracts import ContractError, make_event, validate_event
from app.settings import ROOT


def order():
    return make_event(
        "order",
        "choice-session",
        24400,
        "sim",
        event_id="order-choice",
        order_id="o-choice",
        customer_ref="synthetic-buyer",
        lines=[
            {
                "sku": "SYNTH-B",
                "card_id": "card-b",
                "card_version": 2,
                "variant": {"size": "38", "color": None},
                "qty": 1,
                "unit_price": 200000,
            }
        ],
        amount=200000,
        state="NEEDS_SELLER",
        prev_state="DRAFT",
        actor="assistant",
        approval_needed=True,
        reason="media evidence incomplete; waiting for the shop",
        receipt=None,
    )


def request_schema(name):
    schema = json.loads((ROOT / "contracts" / "requests" / f"{name}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_existing_order_without_candidates_remains_v1():
    event = order()
    validate_event(event)
    assert event["v"] == 1
    assert "candidates" not in event["lines"][0]


def test_order_can_preserve_two_distinct_candidate_ids_in_media_order():
    event = order()
    event["lines"][0]["candidates"] = ["card-b", "card-a"]
    validate_event(event)
    assert event["v"] == 1


@pytest.mark.parametrize(
    "candidates",
    [
        None,
        "card-b",
        [],
        ["card-b"],
        ["card-b", "card-a", "card-c"],
        ["card-b", "card-b"],
        ["card-b", None],
        ["card-b", 2],
        ["card-b", "bad id"],
        ["card-b", ""],
    ],
)
def test_candidates_refuse_missing_duplicate_or_malformed_choices(candidates):
    event = order()
    event["lines"][0]["candidates"] = candidates
    with pytest.raises(ContractError):
        validate_event(event)


@pytest.mark.parametrize("field", ["sku", "card_id", "card_version", "variant", "qty", "unit_price"])
def test_candidates_do_not_replace_required_order_line_fields(field):
    event = order()
    event["lines"][0]["candidates"] = ["card-b", "card-a"]
    del event["lines"][0][field]
    with pytest.raises(ContractError):
        validate_event(event)


def test_candidates_do_not_make_price_nullable_or_change_event_version():
    event = order()
    event["lines"][0]["candidates"] = ["card-b", "card-a"]
    event["lines"][0]["unit_price"] = None
    with pytest.raises(ContractError):
        validate_event(event)
    event["lines"][0]["unit_price"] = 200000
    event["v"] = 2
    with pytest.raises(ContractError):
        validate_event(event)


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "approve"},
        {"decision": "reject"},
        {"decision": "approve", "unit_price": None},
        {"decision": "approve", "unit_price": 210000},
        {"decision": "approve", "card_id": "card-b"},
    ],
)
def test_decision_request_keeps_legacy_bodies_and_adds_an_explicit_item(body):
    request_schema("order-decision").validate(body)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"decision": "keep"},
        {"decision": "approve", "card_id": None},
        {"decision": "approve", "card_id": ""},
        {"decision": "approve", "card_id": "bad id"},
        {"decision": "approve", "card_id": 7},
        {"decision": "approve", "unit_price": -1},
        {"decision": "approve", "unit_price": True},
        {"decision": "approve", "actor": "seller"},
        {"decision": "reject", "card_id": "card-b"},
    ],
)
def test_decision_request_rejects_invalid_shapes_and_caller_supplied_authority(body):
    with pytest.raises(ValidationError):
        request_schema("order-decision").validate(body)


def test_request_schema_cannot_establish_candidate_membership_or_session_history():
    # Syntactically valid, but the route must reject it if the card is not an order candidate in this session.
    request_schema("order-decision").validate({"decision": "approve", "card_id": "unrelated-card"})


@pytest.mark.parametrize("sku", ["SYNTH-B", None])
def test_item_request_expresses_assignment_or_explicitly_keeping_it_unresolved(sku):
    request_schema("card-item").validate({"card_version": 1, "sku": sku})


@pytest.mark.parametrize(
    "body",
    [
        {"sku": "SYNTH-B"},
        {"card_version": 1},
        {"card_version": 0, "sku": "SYNTH-B"},
        {"card_version": True, "sku": "SYNTH-B"},
        {"card_version": "1", "sku": "SYNTH-B"},
        {"card_version": 1, "sku": ""},
        {"card_version": 1, "sku": 7},
        {"card_version": 1, "sku": "SYNTH-B", "price": 150000},
        {"card_version": 1, "sku": "SYNTH-B", "actor": "seller"},
        {"card_version": 1, "sku": None, "status": "live"},
    ],
)
def test_item_request_cannot_set_price_status_or_authority(body):
    with pytest.raises(ValidationError):
        request_schema("card-item").validate(body)


def test_existing_card_event_can_record_a_seller_assigned_unpriced_draft():
    event = make_event(
        "card",
        "choice-session",
        26000,
        "sim",
        event_id="seller-card-v2",
        card_id="unknown-card",
        version=2,
        sku="SYNTH-B",
        name="Synthetic boot",
        price=None,
        currency="VND",
        variants=[{"size": "38", "color": "đen", "stock": 2}],
        stock=2,
        image_url=None,
        status="draft",
        provenance={"frame_ts_ms": 20000, "speech_event_ids": [], "edited_by": "seller"},
    )
    validate_event(event)
    assert event["v"] == 1
    # The event schema cannot compare version 2 or its price to a preceding event.
    unknown_predecessor = copy.deepcopy(event)
    unknown_predecessor["version"] = 99
    validate_event(unknown_predecessor)
