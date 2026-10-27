"""D-024: a priced card changes price only through the seller's route; (card_id, version) is unique. Synthetic events.

Store and ingest refuse a taken version and a price change (409). The route checks in the order of
contracts/README.md "Card versions": 422 price, 404 card, 422 proposal, 409 retired, same price 200 unchanged,
409 old version, then the highest version + 1.
"""

import pytest

from app.contracts import make_event
from app.store import PgStore, VersionTaken

SID = "price1"
PRICE = f"/api/sessions/{SID}/cards/card-a/price"
EVENTS = f"/api/sessions/{SID}/events"


def card(version: int, price: int | None, ts: int = 1000, status: str = "live", **kw) -> dict:
    fields = {
        "card_id": "card-a",
        "version": version,
        "sku": None,
        "name": "Giày trắng",
        "price": price,
        "currency": "VND",
        "variants": [],
        "stock": 0,
        "image_url": None,
        "status": status,
        "provenance": {"frame_ts_ms": None, "speech_event_ids": [], "edited_by": None},
    } | kw
    return make_event("card", SID, ts, "replay", event_id=f"{SID}.card-a.v{version}", **fields)


def proposal(event_id: str = "pp-1", card_id: str = "card-a", heard: int = 220000) -> dict:
    return make_event(
        "price_proposal", SID, 1500, "replay", event_id=event_id, caused_by=["sp-1"], card_id=card_id,
        card_version=1, current_price=250000, heard_price=heard, speech_event_id="sp-1",
    )  # fmt: skip


async def post(client, *events: dict):
    return await client.post(EVENTS, json=list(events))


async def count(store) -> int:
    return len(await store.read(SID, 0, 500))


# store and ingest


async def test_taken_version_under_another_event_id_is_409_and_the_same_event_is_a_duplicate(mem_app):
    _, store, client = mem_app
    v1 = card(1, 250000)
    assert (await post(client, v1)).status_code == 200
    again = (await post(client, v1)).json()["accepted"][0]
    assert again["duplicate"] is True
    other = v1 | {"event_id": "other-writer.v1"}
    r = await post(client, other)
    assert r.status_code == 409 and "card-a v1" in r.json()["detail"]
    assert await count(store) == 1


async def test_memory_store_refuses_a_taken_version():
    from app.store import MemoryStore

    store = MemoryStore()
    await store.open()
    await store.append(card(1, None, status="draft"))
    with pytest.raises(VersionTaken):
        await store.append(card(1, None, status="draft") | {"event_id": "x.v1"})
    assert len(await store.read(SID, 0, 10)) == 1


async def test_pg_store_refuses_a_taken_version_and_keeps_no_event(pg_url):
    store = PgStore(pg_url)
    await store.open()
    sid = f"pytest-ver-{id(store)}"
    try:
        v1 = card(1, None, status="draft") | {"session_id": sid, "event_id": f"{sid}.v1"}
        seq, inserted = await store.append(v1)
        assert inserted
        with pytest.raises(VersionTaken):
            await store.append(v1 | {"event_id": f"{sid}.v1-other"})
        assert [e["event_id"] for e in await store.read(sid, 0, 10)] == [f"{sid}.v1"]
        assert await store.append(v1) == (seq, False)
    finally:
        await store.close()


async def test_ingest_refuses_a_new_price_on_a_priced_card(mem_app):
    _, store, client = mem_app
    await post(client, card(1, 250000))
    r = await post(client, card(2, 220000))
    assert r.status_code == 409 and "/cards/card-a/price" in r.json()["detail"]
    assert await count(store) == 1


async def test_ingest_refuses_null_price_on_a_priced_card(mem_app):
    _, store, client = mem_app
    await post(client, card(1, 250000))
    assert (await post(client, card(2, None))).status_code == 409


async def test_ingest_lets_an_unpriced_card_get_its_price_and_keeps_the_same_price(mem_app):
    _, store, client = mem_app
    assert (await post(client, card(1, None, status="draft"))).status_code == 200
    assert (await post(client, card(2, 250000))).status_code == 200
    assert (await post(client, card(3, 250000, variants=[{"size": "38", "color": None, "stock": 2}], stock=2))).status_code == 200  # fmt: skip
    assert await count(store) == 3


async def test_ingest_batch_stops_at_a_409_and_keeps_the_events_before_it(mem_app):
    _, store, client = mem_app
    await post(client, card(1, 250000))
    before = make_event("speech", SID, 1100, "replay", event_id="sp-b", text="x", final=True, lang="vi",
                        start_ms=1100, end_ms=1100)  # fmt: skip
    after = before | {"event_id": "sp-c"}
    r = await post(client, before, card(2, 220000), after)
    assert r.status_code == 409
    assert [e["event_id"] for e in await store.read(SID, 0, 10)] == [f"{SID}.card-a.v1", "sp-b"]


# route, in the order of the checks


async def test_route_422_price(mem_app):
    _, store, client = mem_app
    await post(client, card(1, 250000))
    for bad in (999, 100_000_001, "220000", True, None):
        r = await client.post(PRICE, json={"price": bad, "card_version": 1})
        assert r.status_code == 422, bad
    assert await count(store) == 1


async def test_route_404_card(mem_app):
    _, _, client = mem_app
    r = await client.post(PRICE, json={"price": 220000, "card_version": 1, "proposal_event_id": "nope"})
    assert r.status_code == 404  # checked before the proposal


async def test_route_422_proposal(mem_app):
    _, store, client = mem_app
    await post(client, card(1, 250000), proposal("pp-b", card_id="card-b"))
    for pid in ("nope", "pp-b", f"{SID}.card-a.v1"):
        r = await client.post(PRICE, json={"price": 220000, "card_version": 1, "proposal_event_id": pid})
        assert r.status_code == 422, pid
    assert await count(store) == 2


async def test_route_409_retired(mem_app):
    _, store, client = mem_app
    await post(client, card(1, None, status="draft"), card(2, None, status="retired"), proposal())
    r = await client.post(PRICE, json={"price": 220000, "card_version": 2, "proposal_event_id": "pp-1"})
    assert r.status_code == 409 and "retired" in r.json()["detail"]
    assert await count(store) == 3


async def test_route_same_price_is_200_unchanged_and_writes_nothing(mem_app):
    _, store, client = mem_app
    await post(client, card(1, 250000))
    r = await client.post(PRICE, json={"price": 250000, "card_version": 1})
    assert (r.status_code, r.json()) == (200, {"changed": False})
    assert await count(store) == 1


async def test_route_same_price_with_an_old_version_is_200_unchanged(mem_app):
    _, store, client = mem_app
    await post(client, card(1, 250000), card(2, 250000, variants=[{"size": "38", "color": None, "stock": 2}], stock=2))  # fmt: skip
    n = await count(store)
    r = await client.post(PRICE, json={"price": 250000, "card_version": 1})
    assert (r.status_code, r.json()) == (200, {"changed": False})
    assert await count(store) == n


async def test_route_409_old_version(mem_app):
    _, store, client = mem_app
    await post(client, card(1, 250000), card(2, 250000, variants=[{"size": "38", "color": None, "stock": 2}], stock=2))  # fmt: skip
    r = await client.post(PRICE, json={"price": 220000, "card_version": 1})
    assert r.status_code == 409 and "version 2" in r.json()["detail"]
    assert await count(store) == 2


async def test_route_uses_a_proposal_and_writes_the_next_version_for_the_seller(mem_app):
    _, store, client = mem_app
    sizes = [{"size": "38", "color": None, "stock": 2}]
    await post(client, card(1, 250000, variants=sizes, stock=2, image_url="/media/a.png"), proposal())
    r = await client.post(PRICE, json={"price": 220000, "card_version": 1, "proposal_event_id": "pp-1"})
    assert (r.status_code, r.json()) == (200, {"changed": True, "version": 2})
    [v1, v2] = await store.card_versions(SID)
    assert (v2["version"], v2["price"], v2["status"]) == (2, 220000, "edited")
    assert v2["provenance"]["edited_by"] == "seller" and v2["caused_by"] == ["pp-1"]
    assert {k: v2[k] for k in ("sku", "name", "variants", "stock", "image_url")} == {
        k: v1[k] for k in ("sku", "name", "variants", "stock", "image_url")
    }
    # a typed price has no proposal; a repeated tap is unchanged
    r = await client.post(PRICE, json={"price": 210000, "card_version": 2})
    assert r.json() == {"changed": True, "version": 3}
    assert "caused_by" not in (await store.card_versions(SID))[-1]
    r = await client.post(PRICE, json={"price": 210000, "card_version": 2})
    assert r.json() == {"changed": False}


async def test_route_409_when_the_store_refuses_the_version(mem_app, monkeypatch):
    _, store, client = mem_app
    await post(client, card(1, 250000))
    real = store.append

    async def taken(event):
        if event["type"] == "card":
            raise VersionTaken(f"{event['card_id']} v{event['version']}")
        return await real(event)

    monkeypatch.setattr(store, "append", taken)
    r = await client.post(PRICE, json={"price": 220000, "card_version": 1})
    assert r.status_code == 409 and "card-a v2" in r.json()["detail"]


# known limit (D-024): "live at t" keeps (ts_ms, version) order


async def test_known_limit_worker_version_stamped_before_the_seller_version(mem_app):
    """A worker version numbered after the seller's but stamped earlier: between the two ts_ms the worker version is
    live and already carries the shop price; from the seller's ts_ms on, the seller version is live."""
    _, store, client = mem_app
    await post(client, card(1, 250000, ts=1000))
    assert (await client.post(PRICE, json={"price": 220000, "card_version": 1})).json()["version"] == 2
    seller_ts = (await store.card_versions(SID))[-1]["ts_ms"]
    sizes = [{"size": "38", "color": None, "stock": 2}]
    worker = card(3, 220000, ts=seller_ts - 1000, variants=sizes, stock=2)
    assert (await post(client, worker)).status_code == 200

    async def live_at(t):
        r = await client.get(f"/api/sessions/{SID}/cards/live", params={"ts_ms": t})
        [c] = r.json()["cards"]
        return c["version"], c["price"]

    assert await live_at(seller_ts - 1001) == (1, 250000)
    assert await live_at(seller_ts - 1000) == (3, 220000)
    assert await live_at(seller_ts - 1) == (3, 220000)
    assert await live_at(seller_ts) == (2, 220000)
