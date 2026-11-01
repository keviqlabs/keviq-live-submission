"""Conservative reuse, jitter, returns and resumed observations without model or database access."""

import copy
import json

import pytest

from app.contracts import make_event
from app.current import current_at
from app.settings import ROOT
from worker.brain import card
from worker.runner import Runner

CATALOG = json.loads((ROOT / "data/comments/c5/catalog.json").read_text())
SID = "continuity"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(card, "CATALOG", copy.deepcopy(CATALOG))
    monkeypatch.setattr(card, "SESSIONS", {})


class NoModel(Runner):
    def client_for(self, event):
        raise AssertionError("Model access is forbidden")


def seen(event_id, category, color, ts):
    return make_event(
        "product_seen", SID, ts, "sim", event_id=event_id, category=category,
        attributes={"color": color, "size": None, "material": None}, confidence=0.99,
        is_new=True, frame_ts_ms=ts, caused_by=["frame-" + event_id],
    )  # fmt: skip


async def ingest(client, runner, event):
    assert (await client.post(f"/api/sessions/{SID}/events", json=[event])).status_code == 200
    await runner.handle(event)
    assert runner.failures == 0


async def history(store):
    return [event for event in await store.read(SID, 0, 100) if event["type"] == "card"]


async def priced_first(store, client, runner):
    await ingest(client, runner, seen("ps-first", "sneaker", "trắng", 1000))
    [first] = await history(store)
    response = await client.post(
        f"/api/sessions/{SID}/cards/{first['card_id']}/price",
        json={"card_version": 1, "price": 320000},
    )
    assert response.status_code == 200
    await runner.handle((await history(store))[-1])
    return first["card_id"]


async def test_category_jitter_after_uncertainty_does_not_restore_the_old_priced_identity(mem_app):
    _, store, client = mem_app
    runner = NoModel("http://test", card.HANDLERS, http=client)
    old = await priced_first(store, client, runner)
    before = await history(store)
    for eid, category, ts in [("ps-changed", "sandal", 2000), ("ps-jitter", "sneaker", 3000)]:
        await ingest(client, runner, seen(eid, category, "trắng", ts))
        opened = [event for event in await history(store) if event.get("caused_by") == [eid]][-1]
        assert opened["card_id"] != old and opened["sku"] is None and opened["price"] is None
        assert opened["variants"] == [] and opened["stock"] == 0 and opened["image_url"] is None
    assert [event for event in await history(store) if event["card_id"] == old] == before


async def test_explicit_return_with_matching_known_observation_keeps_the_distinct_appearance(mem_app):
    _, store, client = mem_app
    runner = NoModel("http://test", card.HANDLERS, http=client)
    old = await priced_first(store, client, runner)
    await ingest(client, runner, seen("ps-shirt", "shirt", "đen", 2000))
    await ingest(client, runner, seen("ps-return", "sneaker", "trắng", 3000))
    returned = (await history(store))[-1]
    assert returned["card_id"] == old and returned["sku"] == "SP02" and returned["price"] == 320000
    assert returned["provenance"]["frame_ts_ms"] == 3000
    assert returned["version"] == 3 and returned["caused_by"] == ["ps-return"]
    assert card.SESSIONS[SID].current == old


async def test_duplicate_and_stale_changed_observations_add_no_versions(mem_app):
    _, store, client = mem_app
    runner = NoModel("http://test", card.HANDLERS, http=client)
    await priced_first(store, client, runner)
    changed = seen("ps-changed", "sandal", "trắng", 3000)
    await ingest(client, runner, changed)
    before = await history(store)
    await runner.handle(changed)
    await ingest(client, runner, seen("ps-stale", "sneaker", "trắng", 2000))
    assert await history(store) == before
    assert not [
        event
        for event in await store.read(SID, 0, 100)
        if event["type"]
        in {
            "model_call",
            "order",
            "price_proposal",
            "message_out",
            "comment_attachment",
        }
    ]


async def test_repeated_reading_of_the_current_unidentified_item_keeps_one_card(mem_app):
    _, store, client = mem_app
    runner = NoModel("http://test", card.HANDLERS, http=client)
    old = await priced_first(store, client, runner)
    for eid, ts in [("ps-sandal-1", 2000), ("ps-sandal-2", 3000), ("ps-sandal-3", 4000)]:
        await ingest(client, runner, seen(eid, "sandal", "trắng", ts))
    cards = await history(store)
    opened = {event["card_id"] for event in cards if event["card_id"] != old}
    assert len(opened) == 1
    [unknown] = opened
    assert not [event for event in cards if event["status"] == "retired"]
    current, previous = current_at(cards, 4500)
    assert current["card_id"] == unknown and current["sku"] is None
    assert previous["card_id"] == old and previous["sku"] == "SP02" and previous["price"] == 320000
    await ingest(client, runner, seen("ps-black", "sandal", "đen", 5000))
    current, previous = current_at(await history(store), 5500)
    assert current["card_id"] not in {old, unknown} and previous["card_id"] == unknown
