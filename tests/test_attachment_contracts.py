"""Structural pre-order attachment contracts, without implementing a producer or decision route."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.contracts import ContractError, event_types, validate_event
from app.settings import ROOT


def candidate(name="b", version=2):
    return {
        "card_id": f"card-{name}",
        "card_version": version,
        "card_event_id": f"card-{name}-v{version}",
        "appearance_event_id": f"card-{name}-appearance",
        "appearance_frame_ts_ms": 20000 if name == "b" else 15000,
    }


def readiness():
    return {
        "after_frame_event_id": "frame-after-comment",
        "handled_frames": [
            {"frame_event_id": "frame-15", "result": "no_product_seen"},
            {"frame_event_id": "frame-19", "result": "read_error"},
            {
                "frame_event_id": "frame-20",
                "result": "product_seen",
                "product_seen_event_id": "seen-20",
                "card_event_ids": ["card-b-v1"],
            },
        ],
    }


def attachment(count=2, action="expire"):
    choices = [candidate("b"), candidate("a")][:count]
    event = {
        "event_id": "attachment-v1",
        "session_id": "attachment-session",
        "ts_ms": 24400,
        "source": "sim",
        "v": 1,
        "type": "comment_attachment",
        "caused_by": ["comment-event", "label-event"],
        "attachment_id": "attachment-1",
        "worker_epoch": "session-epoch-1",
        "version": 1,
        "comment_id": "synthetic-comment",
        "comment_event_id": "comment-event",
        "label_event_id": "label-event",
        "comment_ts_ms": 20400,
        "comment_ingested_ms": 20400,
        "deadline_ms": 24400,
        "snapshot_at_ms": 24400,
        "snapshot_cursor": 18,
        "candidates": choices,
        "outcome": "expired",
        "action": action,
        "selected_card_id": None,
        "actor": "system",
        "reason": "lower frame still pending",
    }
    if action == "ready":
        event.update(outcome="ready", selected_card_id="card-b", reason="frame evidence handled")
        event["readiness_evidence"] = {"observed_cursor": 17, **readiness()}
    elif action in {"select", "keep_unresolved", "reject"}:
        event.update(
            version=2,
            event_id="attachment-v2",
            actor="seller",
            ts_ms=26000,
            caused_by=["attachment-v1", "card-b-v2"],
            reason="seller decision",
        )
        if action == "select":
            event["selected_card_id"] = "card-b"
    return event


def decision_schema():
    schema = json.loads((ROOT / "contracts/requests/attachment-decision.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def system_schema():
    schema = json.loads((ROOT / "contracts/requests/attachment-system.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def system_request(with_evidence=True):
    body = {
        "worker_epoch": "session-epoch-1",
        "comment_event_id": "comment-event",
        "label_event_id": "label-event",
        "observed_cursor": 17,
    }
    if with_evidence:
        body["readiness_evidence"] = readiness()
    return body


def test_attachment_is_a_discovered_v1_event_and_is_listed_in_the_envelope():
    envelope = json.loads((ROOT / "contracts/events/envelope.schema.json").read_text())
    assert "comment_attachment" in event_types()
    assert "comment_attachment" in envelope["properties"]["type"]["enum"]


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("action", ["expire", "keep_unresolved", "reject"])
def test_unselected_expired_history_can_preserve_zero_one_or_two_card_reference_shapes(count, action):
    validate_event(attachment(count, action))


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("action", ["ready", "select"])
def test_a_ready_or_selected_attachment_preserves_a_card_without_price_or_sku_fields(count, action):
    event = attachment(count, action)
    validate_event(event)
    assert event["v"] == 1


def test_a_ready_attachment_can_later_record_seller_rejection_without_clearing_its_choice():
    event = attachment(1, "reject")
    event.update(outcome="ready", selected_card_id="card-b")
    event["readiness_evidence"] = {"observed_cursor": 17, **readiness()}
    validate_event(event)


@pytest.mark.parametrize("field", list(attachment()))
def test_attachment_requires_its_original_clocks_snapshot_and_source_references(field):
    event = attachment()
    del event[field]
    with pytest.raises(ContractError):
        validate_event(event)


@pytest.mark.parametrize("field", ["price", "unit_price", "amount", "order_id", "sku", "stock"])
def test_attachment_is_not_a_placeholder_order_or_price_record(field):
    event = attachment()
    event[field] = 0
    with pytest.raises(ContractError):
        validate_event(event)


@pytest.mark.parametrize("field", list(candidate()))
def test_candidate_requires_card_version_event_and_appearance_evidence(field):
    event = attachment()
    del event["candidates"][0][field]
    with pytest.raises(ContractError):
        validate_event(event)


@pytest.mark.parametrize(
    "choices",
    [
        None,
        [None],
        ["card-b"],
        [candidate("b"), candidate("a"), candidate("c")],
        [candidate(), candidate()],
        [{**candidate(), "price": 0}],
        [{**candidate(), "card_id": "bad id"}],
        [{**candidate(), "card_event_id": None}],
        [{**candidate(), "card_version": 0}],
        [{**candidate(), "card_version": True}],
        [{**candidate(), "appearance_event_id": ""}],
    ],
)
def test_candidate_shape_refuses_fake_missing_oversized_or_duplicate_objects(choices):
    event = attachment()
    event["candidates"] = choices
    with pytest.raises(ContractError):
        validate_event(event)


@pytest.mark.parametrize(
    "change",
    [
        {"version": 0},
        {"version": True},
        {"v": 2},
        {"outcome": "pending"},
        {"action": "expired"},
        {"actor": "customer"},
        {"actor": "seller"},
        {"version": 2},
        {"selected_card_id": "card-b"},
        {"caused_by": []},
        {"caused_by": ["comment-event"]},
        {"caused_by": ["comment-event", "comment-event"]},
    ],
)
def test_initial_expiry_requires_system_actor_null_selection_and_valid_version_shape(change):
    event = attachment()
    event.update(change)
    with pytest.raises(ContractError):
        validate_event(event)


@pytest.mark.parametrize("action", ["ready", "select"])
def test_empty_snapshot_cannot_have_a_selected_item(action):
    with pytest.raises(ContractError):
        validate_event(attachment(0, action))


@pytest.mark.parametrize(
    "change",
    [{"version": 1}, {"actor": "system"}, {"outcome": "ready"}, {"selected_card_id": None}],
)
def test_select_is_a_later_seller_decision_on_an_expired_snapshot(change):
    event = attachment(1, "select")
    event.update(change)
    with pytest.raises(ContractError):
        validate_event(event)


@pytest.mark.parametrize("outcome", ["ready", "expired"])
def test_keep_can_record_an_existing_selection_waiting_for_its_own_sku_or_price(outcome):
    event = attachment(1, "keep_unresolved")
    event.update(selected_card_id="card-b", outcome=outcome)
    if outcome == "ready":
        event["readiness_evidence"] = {"observed_cursor": 17, **readiness()}
    validate_event(event)


@pytest.mark.parametrize(
    "field",
    ["ts_ms", "comment_ts_ms", "comment_ingested_ms", "deadline_ms", "snapshot_at_ms", "snapshot_cursor"],
)
@pytest.mark.parametrize("bad", [-1, True, "24400"])
def test_clocks_and_cursor_require_nonnegative_integer_shapes(field, bad):
    event = attachment()
    event[field] = bad
    with pytest.raises(ContractError):
        validate_event(event)


@pytest.mark.parametrize(
    "body",
    [
        {"attachment_version": 1, "decision": "select", "card_id": "card-b"},
        {"attachment_version": 2, "decision": "keep_unresolved"},
        {"attachment_version": 3, "decision": "reject"},
    ],
)
def test_preorder_decision_has_an_expected_version_and_three_explicit_intents(body):
    decision_schema().validate(body)


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "reject"},
        {"attachment_version": 1},
        {"attachment_version": 0, "decision": "reject"},
        {"attachment_version": True, "decision": "reject"},
        {"attachment_version": "1", "decision": "reject"},
        {"attachment_version": 1, "decision": "approve"},
        {"attachment_version": 1, "decision": "select"},
        {"attachment_version": 1, "decision": "select", "card_id": None},
        {"attachment_version": 1, "decision": "select", "card_id": "bad id"},
        {"attachment_version": 1, "decision": "keep_unresolved", "card_id": "card-b"},
        {"attachment_version": 1, "decision": "reject", "card_id": "card-b"},
    ],
)
def test_preorder_request_rejects_implicit_selection_and_invalid_versions(body):
    with pytest.raises(ValidationError):
        decision_schema().validate(body)


@pytest.mark.parametrize(
    "field", ["actor", "price", "unit_price", "candidates", "deadline_ms", "sku", "outcome"]
)
def test_preorder_request_cannot_supply_authority_price_snapshot_or_deadline(field):
    body = {"attachment_version": 1, "decision": "reject", field: "caller-value"}
    with pytest.raises(ValidationError):
        decision_schema().validate(body)


def test_order_decision_keeps_its_existing_approve_reject_contract():
    schema = json.loads((ROOT / "contracts/requests/order-decision.schema.json").read_text())
    validator = Draft202012Validator(schema)
    validator.validate({"decision": "approve", "card_id": "card-b"})
    with pytest.raises(ValidationError):
        validator.validate({"decision": "keep_unresolved"})


def test_shape_validation_does_not_establish_history_membership_or_chronology():
    event = attachment(2, "select")
    event["selected_card_id"] = "not-a-snapshot-card"
    event["candidates"][1] = candidate("b", 3)  # different object, same logical card: runtime must reject
    event["ts_ms"] = 1  # numeric shape alone cannot prove the decision happened after its deadline
    validate_event(event)
    decision_schema().validate({"attachment_version": 999, "decision": "select", "card_id": "other-session"})


def test_attachment_schema_cannot_prove_a_claimed_seller_was_authorized():
    # Generic ingest must refuse this without the server's write authority, despite its valid event shape.
    forged = copy.deepcopy(attachment(1, "select"))
    validate_event(forged)


@pytest.mark.parametrize("with_evidence", [False, True])
def test_worker_system_request_carries_sources_and_epoch_but_no_outcome_authority(with_evidence):
    system_schema().validate(system_request(with_evidence))


@pytest.mark.parametrize("field", ["worker_epoch", "comment_event_id", "label_event_id", "observed_cursor"])
def test_worker_request_requires_source_cursor_and_session_epoch_shape(field):
    body = system_request()
    del body[field]
    with pytest.raises(ValidationError):
        system_schema().validate(body)


@pytest.mark.parametrize(
    "field",
    [
        "actor",
        "outcome",
        "ready",
        "deadline_ms",
        "candidates",
        "price",
        "attachment_id",
        "version",
        "session_id",
        "credential",
    ],
)
def test_worker_cannot_supply_server_decision_authority_clocks_or_store_fields(field):
    body = system_request()
    body[field] = "caller-value"
    with pytest.raises(ValidationError):
        system_schema().validate(body)


@pytest.mark.parametrize(
    "proof",
    [
        True,
        {},
        {"after_frame_event_id": "frame-after-comment", "handled_frames": []},
        {
            "after_frame_event_id": "frame-after-comment",
            "handled_frames": [{"frame_event_id": "frame-19", "result": "queue_replaced"}],
        },
        {
            "after_frame_event_id": "frame-after-comment",
            "handled_frames": [{"frame_event_id": "frame-20", "result": "product_seen"}],
        },
        {
            "after_frame_event_id": "frame-after-comment",
            "handled_frames": [
                {"frame_event_id": "frame-20", "result": "no_product_seen", "card_event_ids": ["card-b-v1"]}
            ],
        },
        {
            "after_frame_event_id": "frame-after-comment",
            "handled_frames": [
                {
                    "frame_event_id": "frame-20",
                    "result": "product_seen",
                    "product_seen_event_id": "seen-20",
                    "card_event_ids": [],
                }
            ],
        },
    ],
)
def test_worker_evidence_is_not_a_boolean_and_product_results_need_posted_card_refs(proof):
    body = system_request()
    body["readiness_evidence"] = proof
    with pytest.raises(ValidationError):
        system_schema().validate(body)


def test_ready_event_requires_frozen_evidence_while_expired_does_not_claim_readiness():
    event = attachment(1, "ready")
    del event["readiness_evidence"]
    with pytest.raises(ContractError):
        validate_event(event)
    event = attachment()
    event["readiness_evidence"] = {"observed_cursor": 17, **readiness()}
    with pytest.raises(ContractError):
        validate_event(event)


def test_valid_worker_proof_shape_does_not_verify_authentication_coverage_or_actual_reads():
    body = system_request()
    body["worker_epoch"] = "not-the-active-epoch"
    body["readiness_evidence"]["handled_frames"] = [
        {"frame_event_id": "not-a-session-frame", "result": "no_product_seen"}
    ]
    system_schema().validate(body)


@pytest.mark.parametrize("action", ["expire", "select", "keep_unresolved", "reject"])
def test_app_owned_expiry_without_a_worker_claim_and_its_seller_history_keep_null_epoch(action):
    event = attachment(2, action)
    event["worker_epoch"] = None
    validate_event(event)


def test_ready_still_requires_a_non_null_epoch_and_ingress_cannot_omit_its_epoch_authority_shape():
    event = attachment(1, "ready")
    event["worker_epoch"] = None
    with pytest.raises(ContractError):
        validate_event(event)
    body = system_request()
    body["worker_epoch"] = None
    with pytest.raises(ValidationError):
        system_schema().validate(body)


def full_evidence_request(count):
    def identifier(prefix, number):
        return (prefix + str(number)).ljust(96, "x")

    records = [
        {
            "frame_event_id": identifier("frame", i),
            "result": "product_seen",
            "product_seen_event_id": identifier("seen", i),
            "card_event_ids": [identifier("retired", i), identifier("current", i)],
        }
        for i in range(count)
    ]
    return {
        "worker_epoch": identifier("epoch", 1),
        "comment_event_id": identifier("comment", 1),
        "label_event_id": identifier("label", 1),
        "observed_cursor": 2**63 - 1,
        "readiness_evidence": {"after_frame_event_id": identifier("fence", 1), "handled_frames": records},
    }


def test_approved_five_lap_evidence_fits_record_and_one_mib_compact_body_limits():
    body = full_evidence_request(351 * 5)
    system_schema().validate(body)
    assert len(json.dumps(body, separators=(",", ":")).encode("utf-8")) <= 1024 * 1024
    event = attachment(2, "ready")
    event["readiness_evidence"] = {"observed_cursor": body["observed_cursor"], **body["readiness_evidence"]}
    validate_event(event)
    assert len(json.dumps(event, separators=(",", ":")).encode("utf-8")) <= 1024 * 1024


def test_evidence_over_the_five_lap_record_bound_is_rejected_instead_of_sliced():
    body = full_evidence_request(351 * 5 + 1)
    with pytest.raises(ValidationError):
        system_schema().validate(body)
    event = attachment(2, "ready")
    event["readiness_evidence"] = {"observed_cursor": body["observed_cursor"], **body["readiness_evidence"]}
    with pytest.raises(ContractError):
        validate_event(event)


def test_terminal_card_refs_cannot_exceed_the_current_and_retirement_posts():
    body = system_request()
    body["readiness_evidence"]["handled_frames"][-1]["card_event_ids"] = ["card-1", "card-2", "card-3"]
    with pytest.raises(ValidationError):
        system_schema().validate(body)
    event = attachment(2, "ready")
    event["readiness_evidence"]["handled_frames"][-1]["card_event_ids"] = ["card-1", "card-2", "card-3"]
    with pytest.raises(ContractError):
        validate_event(event)
