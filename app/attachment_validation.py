"""Contract validation with linear duplicate checks for bounded full frame evidence."""

import json
from functools import cache

from jsonschema import Draft202012Validator, ValidationError, validators

from app.settings import ROOT


def _key(value):
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        return ("number", value)
    if isinstance(value, dict):
        return ("object", frozenset((k, _key(v)) for k, v in value.items()))
    if isinstance(value, list):
        return ("array", tuple(_key(v) for v in value))
    return (type(value).__name__, value)


def _unique(validator, required, instance, schema):
    if required and validator.is_type(instance, "array"):
        seen = set()
        for value in instance:
            key = _key(value)
            if key in seen:
                yield ValidationError("duplicate array values")
                return
            seen.add(key)


BoundedValidator = validators.extend(Draft202012Validator, {"uniqueItems": _unique})


@cache
def request_validator(name):
    if name not in {"attachment-system", "attachment-decision", "order-decision"}:
        raise ValueError("unknown request contract")
    schema = json.loads((ROOT / "contracts" / "requests" / f"{name}.schema.json").read_text())
    return BoundedValidator(schema)


@cache
def attachment_validator():
    schema = json.loads((ROOT / "contracts/events/comment_attachment.schema.json").read_text())
    return BoundedValidator(schema)
