"""Console API for a card's price (D-024): the seller types a price, or takes a heard price_proposal with one tap.

This route is the only way a priced card changes price; ingest refuses that (`refuse_price_change`). Card writes
from both take one lock, so a price check and its write are one step. No seller identity yet, as with the order
routes: anyone who can reach the console can set a price.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictInt, StrictStr

from app.contracts import ContractError, make_event, now_ms
from app.orders_api import session_events
from app.store import VersionTaken

router = APIRouter()
COPIED = ("sku", "name", "currency", "variants", "stock", "image_url")  # from the latest version, unchanged


class SellerPrice(BaseModel):
    price: Annotated[StrictInt, Field(ge=1000, le=100_000_000)]  # strict: true and "220000" are refused
    card_version: StrictInt
    proposal_event_id: StrictStr | None = None


def card_lock(app) -> asyncio.Lock:
    """Card writes from this route and from ingest go one at a time; one lock per app, made on first use."""
    if getattr(app.state, "card_lock", None) is None:
        app.state.card_lock = asyncio.Lock()
    return app.state.card_lock


async def versions_of(store, session_id: str, card_id: str) -> list[dict]:
    return [c for c in await store.card_versions(session_id) if c["card_id"] == card_id]


async def refuse_price_change(store, event: dict) -> None:
    """409 when an ingested card's price differs from the card's latest non-null price (null differs too)."""
    versions = await versions_of(store, event["session_id"], event["card_id"])
    if any(c["event_id"] == event["event_id"] for c in versions):
        return  # the same event again: the store answers duplicate
    priced = [c for c in versions if c["price"] is not None]
    if priced and event["price"] != (price := max(priced, key=lambda c: c["version"])["price"]):
        raise HTTPException(
            409,
            f"card {event['card_id']} has price {price}; a priced card changes price only through"
            f" POST /api/sessions/{event['session_id']}/cards/{event['card_id']}/price",
        )


@router.post("/api/sessions/{session_id}/cards/{card_id}/price")
async def set_price(session_id: str, card_id: str, body: SellerPrice, request: Request):
    """Checks in the order of contracts/README.md "Card versions". The same price answers 200 before the version
    check, so a repeated tap is safe; "latest" is the highest version, not the one live at some time."""
    store, received = request.app.state.store, now_ms()
    async with card_lock(request.app):
        versions = await versions_of(store, session_id, card_id)
        if not versions:
            raise HTTPException(404, f"no card {card_id} in {session_id}")
        card = max(versions, key=lambda c: c["version"])
        if body.proposal_event_id is not None:
            proposal = next(
                (
                    e
                    for e in await session_events(store, session_id)
                    if e["event_id"] == body.proposal_event_id
                ),
                None,
            )
            if not proposal or proposal["type"] != "price_proposal" or proposal["card_id"] != card_id:
                raise HTTPException(422, f"{body.proposal_event_id} is not a price_proposal of {card_id}")
        if card["status"] == "retired":
            raise HTTPException(409, f"card {card_id} is retired")
        if body.price == card["price"]:
            return {"changed": False}
        if body.card_version != card["version"]:
            raise HTTPException(
                409, f"card {card_id} is at version {card['version']}, not {body.card_version}"
            )
        version = card["version"] + 1
        try:
            event = make_event(
                "card",
                session_id,
                now_ms(),
                card["source"],
                card_id=card_id,
                version=version,
                price=body.price,
                status="edited",
                provenance={**card["provenance"], "edited_by": "seller"},
                caused_by=[body.proposal_event_id] if body.proposal_event_id else [],
                timing={"received_ms": received, "emitted_ms": now_ms()},
                **{k: card[k] for k in COPIED},
            )
        except ContractError as exc:
            raise HTTPException(500, str(exc)) from exc
        try:
            await store.append(event)
        except VersionTaken as exc:
            raise HTTPException(409, f"card {exc} was taken by another writer") from exc
    return {"changed": True, "version": version}
