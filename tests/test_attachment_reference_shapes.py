"""Existing v1 shapes can represent reference-resolved choices; they do not enforce cross-event resolution."""

import pytest

from app.contracts import ContractError, make_event, validate_event
from tests.test_attachment_contracts import attachment


def labeled(kind, sku=None, card_id=None):
    return make_event(
        "comment_labeled",
        "attachment-session",
        20400,
        "sim",
        event_id="label-event",
        comment_id="synthetic-comment",
        label="order",
        action="none",
        scam_signals=[],
        latency_ms=100,
        order_draft={
            "sku": sku,
            "variant": {"size": "38", "color": None},
            "qty": 1,
            "referenced_card_id": card_id,
            "reference_kind": kind,
        },
    )


@pytest.mark.parametrize(
    ("kind", "sku", "target"),
    [
        ("current_card", None, "card-b"),
        ("previous_card", None, "card-a"),
        ("explicit_sku", "SYNTH-A", "card-a"),
        ("explicit_sku", "SYNTH-B", "card-b"),
    ],
)
def test_existing_shapes_can_represent_reference_target_choices_without_reordering_snapshot(
    kind, sku, target
):
    validate_event(labeled(kind, sku, target))
    event = attachment(2, "ready")
    event["selected_card_id"] = target
    validate_event(event)
    assert [c["card_id"] for c in event["candidates"]] == ["card-b", "card-a"]


@pytest.mark.parametrize("kind", ["current_card", "previous_card", "explicit_sku", "unresolved"])
def test_existing_shapes_can_keep_an_unresolved_reference_expired_without_an_automatic_choice(kind):
    validate_event(labeled(kind))
    event = attachment(2, "expire")
    validate_event(event)
    assert event["selected_card_id"] is None


def test_schema_valid_coverage_and_label_shapes_do_not_prove_a_reference_is_resolved():
    # Runtime must reject this proposed automatic choice despite both individual records having valid shapes.
    validate_event(labeled("unresolved"))
    validate_event(attachment(2, "ready"))
    validate_event(labeled("explicit_sku", "OUTSIDE-SNAPSHOT", "other-card"))
    validate_event(attachment(2, "ready"))


def test_reference_vocabulary_is_not_extended_with_a_fallback_kind():
    with pytest.raises(ContractError):
        labeled("fallback_current", card_id="card-b")
