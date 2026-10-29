"""Separate system and seller attachment capabilities; bodies never grant authority."""

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from jsonschema import ValidationError

from app.attachment_auth import require_console
from app.attachment_validation import request_validator
from app.attachments import MAX_BYTES

router = APIRouter()


async def request_body(request, contract):
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "attachment request exceeds 1 MiB")
    try:
        body = json.loads(raw)
        request_validator(contract).validate(body)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "request does not match the decision contract") from exc
    return body


@router.post("/api/sessions/{session_id}/attachments/system")
async def system(session_id: str, request: Request):
    from app.store import VersionTaken

    body = await request_body(request, "attachment-system")
    try:
        result = await request.app.state.attachments.system(
            session_id, body, request.headers.get("authorization")
        )
    except VersionTaken as exc:
        raise HTTPException(409, "canonical attachment identity is already occupied") from exc
    return JSONResponse(result, status_code=202 if result.get("pending") else 200)


@router.post("/api/sessions/{session_id}/attachments/{attachment_id}/decision")
async def seller(session_id: str, attachment_id: str, request: Request):
    from app.store import VersionTaken

    require_console(request.headers.get("authorization"))
    body = await request_body(request, "attachment-decision")
    try:
        return await request.app.state.attachments.seller_decision(session_id, attachment_id, body)
    except VersionTaken as exc:
        raise HTTPException(409, "canonical attachment identity is already occupied") from exc
