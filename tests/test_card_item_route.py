"""Seller-only identity assignment on an unresolved draft; memory events and synthetic catalog only."""

import asyncio
import copy

import pytest

from app import cards_api
from app.contracts import make_event
from app.store import VersionTaken

SID = "item-choice"
URL = f"/api/sessions/{SID}/cards/unknown/item"
PRICE = f"/api/sessions/{SID}/cards/unknown/price"
EVENTS = f"/api/sessions/{SID}/events"
CATALOG = [
    {"sku": "SYNTH-A", "name": "Synthetic shoe", "price": 999000,
     "variants": [{"size": "38", "color": "trắng", "stock": 2}]},
    {"sku": "SYNTH-B", "name": "Synthetic shirt", "price": 888000,
     "variants": [{"size": "M", "color": "đen", "stock": 3}]},
]  # fmt: skip


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    monkeypatch.setattr(cards_api, "CATALOG", copy.deepcopy(CATALOG), raising=False)


def draft(version=1, **changes):
    fields = {
        "card_id": "unknown", "version": version, "sku": None, "name": "Observed item",
        "price": None, "currency": "VND", "variants": [], "stock": 0,
        "image_url": "/synthetic/item.png", "status": "draft",
        "provenance": {"frame_ts_ms": 2000, "speech_event_ids": ["speech-a"], "edited_by": None},
    } | changes  # fmt: skip
    return make_event("card", SID, 2000, "sim", event_id=f"unknown-v{version}", **fields)


async def rows(store):
    return await store.read(SID, 0, 100)


async def seed(client, *events):
    response = await client.post(EVENTS, json=list(events))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("price", [None, 123000])
async def test_assignment_appends_identity_without_price_status_or_history_effects(mem_app, price):
    _, store, client = mem_app
    await seed(client, draft(price=price))
    before = await rows(store)
    response = await client.post(URL, json={"card_version": 1, "sku": "SYNTH-A"})
    assert (response.status_code, response.json()) == (200, {"changed": True, "version": 2})
    after = await rows(store)
    assert after[:-1] == before
    result = after[-1]
    assert result["type"] == "card" and result["card_id"] == "unknown"
    assert (result["sku"], result["name"], result["variants"], result["stock"]) == (
        "SYNTH-A",
        CATALOG[0]["name"],
        CATALOG[0]["variants"],
        2,
    )
    assert (result["price"], result["status"], result["image_url"]) == (price, "draft", "/synthetic/item.png")
    assert result["currency"] == before[0]["currency"]
    assert result["provenance"] == {**before[0]["provenance"], "edited_by": "seller"}
    assert result["caused_by"] == [before[0]["event_id"]]
    assert result["ts_ms"] >= before[0]["ts_ms"]
    assert {event["type"] for event in after} == {"card"}
    # Selection cannot be repeated or used to reassign a known card, even with the same SKU.
    for sku in ("SYNTH-A", "SYNTH-B", None):
        assert (await client.post(URL, json={"card_version": 2, "sku": sku})).status_code == 409
    assert await rows(store) == after


async def test_keep_unresolved_is_noop_after_the_same_latest_version_checks(mem_app):
    _, store, client = mem_app
    await seed(client, draft(), draft(2))
    before = await rows(store)
    stale = await client.post(URL, json={"card_version": 1, "sku": None})
    assert stale.status_code == 409
    for _ in range(2):
        response = await client.post(URL, json={"card_version": 2, "sku": None})
        assert response.json() == {"changed": False, "version": 2}
    assert await rows(store) == before


@pytest.mark.parametrize("status", ["live", "edited", "retired"])
@pytest.mark.parametrize("sku", [None, "SYNTH-A"])
async def test_legacy_non_draft_without_sku_stays_ineligible(mem_app, status, sku):
    _, store, client = mem_app
    await seed(client, draft(status=status, price=123000))
    before = await rows(store)
    assert (await client.post(URL, json={"card_version": 1, "sku": sku})).status_code == 409
    assert await rows(store) == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"sku": "SYNTH-A"},
        {"card_version": 1},
        {"card_version": True, "sku": "SYNTH-A"},
        {"card_version": 0, "sku": None},
        {"card_version": "1", "sku": None},
        {"card_version": 1, "sku": ""},
        {"card_version": 1, "sku": 5},
        {"card_version": 1, "sku": None, "actor": "seller"},
        {"card_version": 1, "sku": "SYNTH-A", "price": 123000},
        {"card_version": 1, "sku": "SYNTH-A", "status": "live"},
    ],
)
async def test_invalid_request_cannot_supply_privilege_price_or_status(mem_app, body):
    _, store, client = mem_app
    await seed(client, draft())
    before = await rows(store)
    assert (await client.post(URL, json=body)).status_code == 422
    assert await rows(store) == before


async def test_session_card_version_and_exact_catalog_membership_are_required(mem_app):
    _, store, client = mem_app
    body = {"card_version": 1, "sku": "SYNTH-A"}
    assert (await client.post(URL, json=body)).status_code == 404
    await seed(client, draft(), draft(2))
    assert (await client.post(URL.replace(SID, "another-session"), json=body)).status_code == 404
    assert (await client.post(URL.replace("unknown", "another-card"), json=body)).status_code == 404
    assert (await client.post(URL, json=body)).status_code == 409
    before = await rows(store)
    for sku in ("synth-a", "SYNTH-A ", "UNKNOWN"):
        assert (await client.post(URL, json={"card_version": 2, "sku": sku})).status_code == 422
    assert await rows(store) == before


async def test_worker_credential_does_not_authorize_sku_or_price(mem_app):
    _, store, client = mem_app
    await seed(client, draft())
    before = await rows(store)
    headers = {"authorization": "Bearer synthetic-worker"}
    assert (
        await client.post(URL, json={"card_version": 1, "sku": "SYNTH-A"}, headers=headers)
    ).status_code == 403
    assert (
        await client.post(PRICE, json={"card_version": 1, "price": 123000}, headers=headers)
    ).status_code == 403
    assert await rows(store) == before


async def test_generic_ingest_cannot_assign_or_reassign_an_existing_sku(mem_app):
    _, store, client = mem_app
    await seed(client, draft())
    assert (await client.post(EVENTS, json=[draft(2, sku="SYNTH-A")])).status_code == 409
    assert (await client.post(URL, json={"card_version": 1, "sku": "SYNTH-A"})).status_code == 200
    before = await rows(store)
    assert (await client.post(EVENTS, json=[draft(3, sku="SYNTH-B")])).status_code == 409
    assert await rows(store) == before


async def test_two_simultaneous_item_choices_have_one_append(mem_app):
    _, store, client = mem_app
    await seed(client, draft())
    responses = await asyncio.gather(
        *[client.post(URL, json={"card_version": 1, "sku": sku}) for sku in ("SYNTH-A", "SYNTH-B")]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert [event["version"] for event in await rows(store)] == [1, 2]


async def test_price_and_item_choice_use_the_same_card_lock(mem_app):
    _, store, client = mem_app
    await seed(client, draft())
    responses = await asyncio.gather(
        client.post(URL, json={"card_version": 1, "sku": "SYNTH-A"}),
        client.post(PRICE, json={"card_version": 1, "price": 123000}),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert [event["version"] for event in await rows(store)] == [1, 2]


async def test_store_version_conflict_returns_409_without_an_effect(mem_app, monkeypatch):
    _, store, client = mem_app
    await seed(client, draft())
    before = await rows(store)

    async def taken(event):
        raise VersionTaken("unknown v2")

    monkeypatch.setattr(store, "append", taken)
    assert (await client.post(URL, json={"card_version": 1, "sku": "SYNTH-A"})).status_code == 409
    assert await rows(store) == before
