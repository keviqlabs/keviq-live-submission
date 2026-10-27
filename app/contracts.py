"""Validate events against contracts/events/*.schema.json."""

import json
import time
import uuid
from functools import cache

from jsonschema import Draft202012Validator

from app.settings import ROOT

EVENTS_DIR = ROOT / "contracts" / "events"


class ContractError(ValueError):
    pass


@cache
def validators() -> dict[str, Draft202012Validator]:
    out = {}
    for path in sorted(EVENTS_DIR.glob("*.schema.json")):
        name = path.name.removesuffix(".schema.json")
        if name == "envelope":
            continue
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        out[name] = Draft202012Validator(schema)
    return out


def event_types() -> list[str]:
    return sorted(validators())


def validate_event(event: dict) -> dict:
    etype = event.get("type") if isinstance(event, dict) else None
    validator = validators().get(etype)
    if validator is None:
        raise ContractError(f"unknown event type: {etype!r}")
    errors = sorted(validator.iter_errors(event), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:5])
        raise ContractError(f"{etype} violates contract: {detail}")
    return event


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def make_event(etype: str, session_id: str, ts_ms: int, source: str, **fields) -> dict:
    """Build and validate a contract event; `caused_by`/`timing` go in fields when present."""
    event = {
        "event_id": fields.pop("event_id", None) or new_id(etype),
        "session_id": session_id,
        "ts_ms": ts_ms,
        "source": source,
        "v": 1,
        "type": etype,
        **{k: v for k, v in fields.items() if not (k in ("caused_by", "timing") and not v)},
    }
    return validate_event(event)
