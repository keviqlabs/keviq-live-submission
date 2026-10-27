"""worker/brain/card.py: a different spoken price on a priced card is a price_proposal for the seller (D-024).

The seller takes it through POST /cards/{card_id}/price; the worker's later versions keep the seller's price, also
when the worker's version number was taken first (409: re-read the highest version, apply on top). Synthetic
events; speech goes through the rule reader (the model reply is an error), 0 USD.
"""

from app.contracts import now_ms
from tests.test_card import SID, cards_in, fresh, hear, run, runner_with, said, seen  # noqa: F401
from tests.test_card_handoff import model_down


async def proposals(store) -> list[dict]:
    return [e for e in await store.read(SID, 0, 500) if e["type"] == "price_proposal"]


async def live_at_250(client, store, runner, ts: int) -> dict:
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    [live] = await hear(client, store, runner, said("sp-1", "đôi này 250 nghìn", ts + 1000))
    assert (live["card_id"], live["version"], live["price"]) == ("card-a", 2, 250000)
    return live


async def test_a_new_spoken_price_is_one_proposal_and_the_card_keeps_its_price(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(4))
    ts = now_ms()
    await live_at_250(client, store, runner, ts)
    assert await hear(client, store, runner, said("sp-2", "đôi này 220 nghìn", ts + 5000)) == []
    [p] = await proposals(store)
    assert {
        k: p[k] for k in ("card_id", "card_version", "current_price", "heard_price", "speech_event_id")
    } == {
        "card_id": "card-a",
        "card_version": 2,
        "current_price": 250000,
        "heard_price": 220000,
        "speech_event_id": "sp-2",
    }
    assert (p["ts_ms"], p["caused_by"]) == (ts + 5000, ["sp-2"])
    # the same heard price again is not proposed again; another heard price is
    assert await hear(client, store, runner, said("sp-3", "đôi này 220 nghìn", ts + 9000)) == []
    assert len(await proposals(store)) == 1
    assert await hear(client, store, runner, said("sp-4", "đôi này 210 nghìn", ts + 12000)) == []
    assert [p["heard_price"] for p in await proposals(store)] == [220000, 210000]
    assert [c["price"] for c in await cards_in(store)] == [None, 250000]


async def test_a_card_without_a_price_takes_the_spoken_price_and_gets_no_proposal(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    await live_at_250(client, store, runner, now_ms())
    assert await proposals(store) == []


async def test_the_seller_takes_a_proposal_and_later_worker_versions_keep_the_price(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(5))
    ts = now_ms()
    await live_at_250(client, store, runner, ts)
    await hear(client, store, runner, said("sp-2", "đôi này 220 nghìn", ts + 5000))
    [p] = await proposals(store)
    body = {"price": 220000, "card_version": 2, "proposal_event_id": p["event_id"]}
    r = await client.post(f"/api/sessions/{SID}/cards/card-a/price", json=body)
    assert r.json() == {"changed": True, "version": 3}
    seller = (await cards_in(store))[-1]
    assert (seller["price"], seller["status"], seller["caused_by"]) == (220000, "edited", [p["event_id"]])
    assert seller["provenance"]["edited_by"] == "seller"
    await runner.handle(seller)  # the worker reads the seller's version from the stream
    [nxt] = await hear(client, store, runner, said("sp-3", "size 38 nhé", ts + 9000))
    assert (nxt["version"], nxt["price"], nxt["status"]) == (4, 220000, "edited")
    assert [v["size"] for v in nxt["variants"]] == ["38"] and nxt["provenance"]["edited_by"] == "seller"
    # at the new price, 220 is nothing to propose and 250 is a new proposal
    await hear(client, store, runner, said("sp-4", "đôi này 220 nghìn", ts + 12000))
    await hear(client, store, runner, said("sp-5", "đôi này 250 nghìn", ts + 15000))
    assert [(p["card_version"], p["current_price"], p["heard_price"]) for p in await proposals(store)] == [
        (2, 250000, 220000),
        (4, 220000, 250000),
    ]


async def test_worker_409_reads_the_highest_version_and_applies_its_sizes_on_top(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(2))
    ts = now_ms()
    await live_at_250(client, store, runner, ts)
    r = await client.post(
        f"/api/sessions/{SID}/cards/card-a/price", json={"price": 220000, "card_version": 2}
    )
    assert r.json() == {"changed": True, "version": 3}
    # the worker has not seen v3 yet, so its sizes version is numbered 3 and refused
    [nxt] = await hear(client, store, runner, said("sp-3", "size 38 nhé", ts + 9000))
    assert (nxt["version"], nxt["price"], nxt["status"]) == (4, 220000, "edited")
    assert [v["size"] for v in nxt["variants"]] == ["38"]
    assert nxt["provenance"]["speech_event_ids"] == ["sp-1", "sp-3"]
    versions = await cards_in(store)
    assert [(c["version"], c["price"]) for c in versions] == [
        (1, None),
        (2, 250000),
        (3, 220000),
        (4, 220000),
    ]
    assert [c["version"] for c in versions if c.get("caused_by", [None])[0] == "sp-3"] == [
        4
    ]  # 3 is never reused


async def test_worker_409_drops_a_price_the_seller_already_set(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    r = await client.post(
        f"/api/sessions/{SID}/cards/card-a/price", json={"price": 300000, "card_version": 1}
    )
    assert r.json() == {"changed": True, "version": 2}
    assert await hear(client, store, runner, said("sp-1", "đôi này 250 nghìn", ts + 1000)) == []
    assert [(c["version"], c["price"], c["status"]) for c in await cards_in(store)] == [
        (1, None, "draft"),
        (2, 300000, "edited"),
    ]
