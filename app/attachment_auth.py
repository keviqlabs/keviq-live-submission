"""Server-provisioned, session-scoped capabilities; request fields cannot register a worker."""

import hashlib
import hmac
import re

from fastapi import HTTPException

SOURCE_KINDS = frozenset(
    {
        "frame",
        "speech",
        "comment",
        "comment_labeled",
        "product_seen",
        "card",
        "model_call",
        "price_proposal",
        "message_in",
    }
)


# How long after its ingestion a comment waits for its attachment card before it expires.
WAIT_MS = 10_000


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bearer_digest(header: str | None):
    if not header or not header.startswith("Bearer ") or not header[7:]:
        raise HTTPException(401, "a scoped credential is required")
    return token_digest(header[7:])


def binding_definition(session_id, epoch, system_token, sources, *, wait_ms=WAIT_MS, ordered_media=True):
    """Trusted provisioning API, not an HTTP route. Persist digests only; callers supply actual credentials."""
    if (
        not isinstance(epoch, str)
        or not re.fullmatch(r"[A-Za-z0-9._:-]{1,96}", epoch)
        or not isinstance(session_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id)
        or type(wait_ms) is not int
        or wait_ms <= 0
    ):
        raise ValueError("invalid session binding")
    if not system_token:
        raise ValueError("a system credential is required")
    grants = []
    digests = {token_digest(system_token)}
    for source in sources:
        digest = token_digest(source["token"])
        if digest in digests or not source["principal"] or not source["kinds"]:
            raise ValueError("source capabilities must be distinct and scoped")
        kinds = source["kinds"]
        if not set(kinds) <= SOURCE_KINDS or any(
            not values or not set(values) <= {"sim", "replay", "facebook"} for values in kinds.values()
        ):
            raise ValueError("invalid source capability")
        digests.add(digest)
        grants.append(
            {
                "digest": digest,
                "principal": source["principal"],
                "kinds": {kind: list(values) for kind, values in kinds.items()},
            }
        )
    if sum("frame" in g["kinds"] for g in grants) != 1:
        raise ValueError("one authoritative frame source is required")
    return {
        "session_id": session_id,
        "stream_id": session_id,
        "epoch": epoch,
        "system_digest": token_digest(system_token),
        "sources": grants,
        "wait_ms": wait_ms,
        "ordered_media": bool(ordered_media),
        "active": True,
    }


def authorize(binding, header, *, epoch=None, kind=None, source=None):
    if binding is None:
        raise HTTPException(503, "attachment ingress is not configured")
    supplied = bearer_digest(header)
    if not binding.get("active"):
        raise HTTPException(403, "worker epoch has been revoked")
    if epoch is not None and epoch != binding["epoch"]:
        raise HTTPException(403, "worker epoch does not own this session")
    system = hmac.compare_digest(supplied, binding["system_digest"])
    grant = next((g for g in binding["sources"] if hmac.compare_digest(supplied, g["digest"])), None)
    if kind is None:
        if grant:
            raise HTTPException(403, "source capability cannot create a system outcome")
        if not system:
            raise HTTPException(401, "invalid worker credential")
        return {"epoch": binding["epoch"], "principal": "system"}
    if system:
        raise HTTPException(403, "system capability cannot write generic events")
    if grant is None:
        raise HTTPException(401, "invalid source credential")
    if kind not in grant["kinds"] or source not in grant["kinds"][kind]:
        raise HTTPException(403, "source capability does not cover this event")
    return {"epoch": binding["epoch"], "principal": grant["principal"]}


def require_console(header):
    if header:
        raise HTTPException(403, "worker credentials do not authorize seller actions")
