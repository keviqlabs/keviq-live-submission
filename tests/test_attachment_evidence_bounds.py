"""Full request size and source-coverage limits, with synthetic stored evidence only."""

import json
import time

import pytest
from jsonschema import Draft202012Validator

from app.attachment_validation import BoundedValidator, attachment_validator, request_validator
from app.contracts import make_event
from tests.test_attachments_runtime import world  # noqa: F401


def full_id(prefix, number=0):
    return (prefix + f"{number:08d}").ljust(96, "x")


def worst_case_body():
    return {
        "worker_epoch": full_id("epoch"),
        "comment_event_id": full_id("comment"),
        "label_event_id": full_id("label"),
        "observed_cursor": 9223372036854775807,
        "readiness_evidence": {
            "after_frame_event_id": full_id("after"),
            "handled_frames": [
                {
                    "frame_event_id": full_id("frame", i),
                    "result": "product_seen",
                    "product_seen_event_id": full_id("seen", i),
                    "card_event_ids": [full_id("card-a", i), full_id("card-b", i)],
                }
                for i in range(1755)
            ],
        },
    }


def test_full_1755_record_request_fits_published_raw_bound():
    body = worst_case_body()
    request_validator("attachment-system").validate(body)
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    print(
        "FULL_EVIDENCE_BODY "
        + json.dumps(
            {
                "records": 1755,
                "card_refs_per_record": 2,
                "id_chars": 96,
                "cursor_max": 9223372036854775807,
                "serializer": "json.dumps ensure_ascii=False separators=(',', ':') UTF-8",
                "full_body_bytes": len(raw),
                "bound_bytes": 1048576,
                "remaining_bytes": 1048576 - len(raw),
            }
        )
    )
    assert len(raw) <= 1048576
    event = {
        "type": "comment_attachment",
        "v": 1,
        "source": "replay",
        "session_id": "s" * 64,
        "event_id": full_id("event"),
        "attachment_id": full_id("attachment"),
        "version": 1,
        "ts_ms": 9223372036854775807,
        "worker_epoch": body["worker_epoch"],
        "caused_by": [body["comment_event_id"], body["label_event_id"]],
        "comment_id": full_id("cid"),
        "comment_event_id": body["comment_event_id"],
        "label_event_id": body["label_event_id"],
        "comment_ts_ms": 9223372036854771807,
        "comment_ingested_ms": 9223372036854771807,
        "deadline_ms": 9223372036854775807,
        "snapshot_at_ms": 9223372036854775807,
        "snapshot_cursor": 9223372036854775807,
        "candidates": [
            {
                "card_id": full_id("choice", i),
                "card_version": 9223372036854775807,
                "card_event_id": full_id("version", i),
                "appearance_event_id": full_id("appearance", i),
                "appearance_frame_ts_ms": 9223372036854771807,
            }
            for i in range(2)
        ],
        "outcome": "ready",
        "action": "ready",
        "selected_card_id": full_id("choice", 0),
        "actor": "system",
        "reason": "ordered media coverage and reference resolved",
        "readiness_evidence": {"observed_cursor": body["observed_cursor"], **body["readiness_evidence"]},
    }
    attachment_validator().validate(event)
    event_raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    print(
        "FULL_EVIDENCE_EVENT "
        + json.dumps(
            {
                "records": 1755,
                "session_chars": 64,
                "id_chars": 96,
                "number_domain_max": 9223372036854775807,
                "full_event_bytes": len(event_raw),
                "remaining_bytes": 1048576 - len(event_raw),
                "bound_bytes": 1048576,
                "scope": "Declared bounded number/identifier domain and actual emitted field set, not an absolute schema maximum.",
            }
        )
    )
    assert len(event_raw) <= 1048576


@pytest.mark.parametrize(
    "values",
    [
        [True, 1],
        [False, 0],
        [1, 1.0],
        [None, None],
        [{"a": 1, "b": [False]}, {"b": [False], "a": 1}],
        [[1, False], [1, 0]],
        ["a", "b"],
        [{"a": 1}, {"a": 2}],
        [[1, {"x": 2}], [1, {"x": 2}]],
    ],
)
def test_linear_duplicate_check_preserves_json_schema_equality(values):
    schema = {"type": "array", "uniqueItems": True}
    assert BoundedValidator(schema).is_valid(values) == Draft202012Validator(schema).is_valid(values)


@pytest.mark.parametrize("records,ready", [(1754, True), (1755, True), (1756, False)])
async def test_full_stored_evidence_covers_admitted_prefix_without_truncation(world, records, ready):  # noqa: F811
    world.now = 10**13
    origin = {"principal": "synthetic-source", "epoch": world.epoch, "role": "source", "binding_revision": 1}
    frame_ids, terminals = [], []
    async with world.store.session_transaction(world.sid), world.store.source_write(origin):
        for i in range(records):
            ts = (i + 1) * 1000
            frame = make_event(
                "frame",
                world.sid,
                ts,
                "replay",
                event_id=full_id(world.sid + "-frame", i),
                jpeg_path="synthetic/frame.jpg",
                width=10,
                height=10,
            )
            await world.store.append(frame)
            frame_ids.append(frame["event_id"])
            seen = make_event(
                "product_seen",
                world.sid,
                ts,
                "replay",
                event_id=full_id(world.sid + "-seen", i),
                category="sneaker",
                attributes={},
                confidence=1,
                is_new=True,
                frame_ts_ms=ts,
                caused_by=[frame["event_id"]],
            )
            await world.store.append(seen)
            cards = []
            for version in (1, 2):
                card = make_event(
                    "card",
                    world.sid,
                    world.now,
                    "replay",
                    event_id=full_id(world.sid + "-card-" + str(version), i),
                    card_id=full_id(world.sid + "-identity", i),
                    version=version,
                    sku="GIAY-01",
                    name="Synthetic item",
                    price=None,
                    currency="VND",
                    status="draft",
                    stock=1,
                    variants=[],
                    image_url=None,
                    provenance={"frame_ts_ms": ts, "speech_event_ids": [], "edited_by": None},
                    caused_by=[seen["event_id"]],
                )
                await world.store.append(card)
                cards.append(card["event_id"])
            terminals.append(
                {
                    "frame_event_id": frame["event_id"],
                    "result": "product_seen",
                    "product_seen_event_id": seen["event_id"],
                    "card_event_ids": cards,
                }
            )
    pair = await world.comment(ts=records * 1000 + 500)
    after = await world.frame((records + 1) * 1000)
    body = {
        "worker_epoch": world.epoch,
        "comment_event_id": pair[0]["event_id"],
        "label_event_id": pair[1]["event_id"],
        "observed_cursor": await world.store.head_seq(),
        "readiness_evidence": {
            "after_frame_event_id": after["event_id"],
            "handled_frames": terminals if records <= 1755 else terminals[:-1],
        },
    }
    started = time.monotonic()
    response = await world.system_request(body)
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    print(
        "FULL_EVIDENCE_CHECK "
        + json.dumps(
            {
                "handled_records": records,
                "admitted_frames": records + 1,
                "full_body_bytes": len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()),
                "http_check_ms": elapsed_ms,
                "ready": ready,
                "store": world.store.kind,
                "timing_gate_proven": False,
            }
        )
    )
    if ready:
        assert response["attachment"]["outcome"] == "ready"
        assert len(response["attachment"]["readiness_evidence"]["handled_frames"]) == records
    else:
        assert response["pending"] is True
        assert not await world.store.attachments(world.sid)
    assert not await world.store.orders(world.sid)  # Real selected card is still unpriced.
