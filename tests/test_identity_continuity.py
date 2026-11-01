"""Conservative reuse, jitter, returns and resumed observations without model or database access."""

import copy
import json

import pytest

from app.contracts import make_event
from app.current import current_at
from app.settings import ROOT
from measure.core import c1
from orders import flow
from orders import handlers as orders_handlers
from worker.brain import card, labels
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


async def test_each_repeated_reading_is_a_version_of_the_kept_card(mem_app):
    """C1 (D-009): every product_seen has a consequence. A repeat moves only the kept card's frame."""
    _, store, client = mem_app
    runner = NoModel("http://test", card.HANDLERS, http=client)
    await priced_first(store, client, runner)
    await ingest(client, runner, seen("ps-sandal-1", "sandal", "trắng", 2000))
    [opened] = [event for event in await history(store) if event.get("caused_by") == ["ps-sandal-1"]]
    for eid, ts in [("ps-sandal-2", 3000), ("ps-sandal-3", 4000)]:
        before = await history(store)
        await ingest(client, runner, seen(eid, "sandal", "trắng", ts))
        [version] = (await history(store))[len(before) :]
        kept = before[-1]
        assert version["card_id"] == kept["card_id"] == opened["card_id"]
        assert version["caused_by"] == [eid] and version["version"] == kept["version"] + 1
        assert version["provenance"] == {**kept["provenance"], "frame_ts_ms": ts}
        same = ("sku", "name", "price", "status", "variants", "stock", "image_url")
        assert {k: version[k] for k in same} == {k: kept[k] for k in same}
    report = c1(await store.read(SID, 0, 100))
    assert report["n"] == 4 and report["missing"] == []


async def test_a_second_reading_of_a_priced_item_without_sku_sets_nothing_and_confirms_nothing(mem_app):
    """The merge also takes a live (here seller-priced, `edited`), priced card without SKU: the second reading
    sets no SKU and no price, and an order comment on that card waits for the seller."""
    _, store, client = mem_app
    cards = NoModel("http://test", card.HANDLERS, http=client)
    await ingest(client, cards, seen("ps-other-1", "other", "trắng", 1000))
    [first] = await history(store)
    response = await client.post(
        f"/api/sessions/{SID}/cards/{first['card_id']}/price",
        json={"card_version": 1, "price": 550000},
    )
    assert response.status_code == 200
    priced = (await history(store))[-1]
    await cards.handle(priced)
    assert priced["status"] in flow.LIVE and priced["sku"] is None and priced["price"] == 550000
    await ingest(client, cards, seen("ps-other-2", "other", "trắng", 1500))

    versions = await history(store)
    item = {"reference_kind": "current_card", "sku": None, "size": None, "color": None, "qty": 1}
    draft = labels.resolve(item, versions, 2500, {c["sku"] for c in CATALOG})
    comment = make_event(
        "comment", SID, 2500, "sim", comment_id="cm-other", user_ref="synthetic-buyer",
        text="chốt cái này", reply_to=None,
    )  # fmt: skip
    label = make_event(
        "comment_labeled", SID, 2500, "sim", comment_id="cm-other", label="order", order_draft=draft,
        scam_signals=[], action="none", latency_ms=0, caused_by=[comment["event_id"]],
    )  # fmt: skip
    orders = Runner("http://test", orders_handlers.HANDLERS, http=client)
    await ingest(client, orders, comment)
    await ingest(client, orders, label)

    events = await store.read(SID, 0, 100)
    [repeat] = [
        event for event in events if event["type"] == "card" and event.get("caused_by") == ["ps-other-2"]
    ]
    assert repeat["card_id"] == first["card_id"] and repeat["provenance"]["frame_ts_ms"] == 1500
    assert {event["card_id"] for event in events if event["type"] == "card"} == {first["card_id"]}
    assert [(event["sku"], event["price"]) for event in await history(store)] == [
        (None, None),
        (None, 550000),
        (None, 550000),
    ]
    states = [event for event in events if event["type"] == "order"]
    assert [event["state"] for event in states] == ["DRAFT", "NEEDS_SELLER"]
    assert states[-1]["lines"][0]["card_id"] == first["card_id"]
    assert not [
        event for event in events if event["type"] == "message_out" and event["template"] == "confirm_order"
    ]
