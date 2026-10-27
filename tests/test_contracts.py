import json

import pytest

from app.contracts import ContractError, event_types, make_event, validate_event
from app.settings import ROOT


def test_every_type_has_a_schema_and_the_envelope_lists_them():
    envelope = json.loads((ROOT / "contracts/events/envelope.schema.json").read_text())
    assert sorted(envelope["properties"]["type"]["enum"]) == sorted(event_types())
    assert set(event_types()) >= {
        "frame",
        "speech",
        "product_seen",
        "card",
        "comment",
        "comment_labeled",
        "order",
        "message_out",
        "message_in",
        "model_call",
    }


def test_demo_session_is_valid(demo_events):
    for e in demo_events:
        e = {k: v for k, v in e.items() if k != "_replay_at_ms"}
        validate_event(e)


def test_unknown_field_and_bad_enum_are_rejected():
    e = make_event("comment", "s1", 1, "sim", comment_id="c1", user_ref="u1", text="hi", reply_to=None)
    with pytest.raises(ContractError):
        validate_event({**e, "phone": "0900000001"})
    with pytest.raises(ContractError):
        validate_event({**e, "source": "tiktok"})


def test_frame_needs_exactly_one_image_field():
    base = {
        "event_id": "f1",
        "session_id": "s1",
        "ts_ms": 1,
        "source": "sim",
        "v": 1,
        "type": "frame",
        "width": 640,
        "height": 360,
    }
    validate_event({**base, "jpeg_path": "a.jpg"})
    with pytest.raises(ContractError):
        validate_event(base)
    with pytest.raises(ContractError):
        validate_event({**base, "jpeg_path": "a.jpg", "jpeg_b64": "AAAA"})
