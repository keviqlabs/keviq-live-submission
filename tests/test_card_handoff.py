"""worker/brain/card.py: which card a spoken price lands on when products change. Synthetic events only.

Authority: a price is attached only when the card is certain; when it is not, nothing is written; a card that
already has a price is not repriced by speech (a new price is a price_proposal for the seller, D-023, D-024). A
speech still applies to whatever card is current when it is handled, so the price-keeping rule is what protects a
priced card:
- the next product's price arrives before its product_seen, while the previous live card is current;
- a clear new price for a live or seller-edited card waits for the shop; sizes from the same speech still apply.
Speech goes through the rule reader (the model reply is an error), so the wording is what the card reads.
"""

import logging

from app.contracts import make_event, now_ms
from meta.transport import Reply
from tests.test_card import SID, cards_in, fresh, hear, run, runner_with, said, seen, settle  # noqa: F401
from worker.brain import card


def model_down(n: int) -> list[Reply]:
    return [Reply(400, {"error": "bad"}) for _ in range(n)]


async def prices(store, card_id: str) -> list[int]:
    return [c["price"] for c in await cards_in(store) if c["card_id"] == card_id and c["price"] is not None]


async def test_fast_change_prices_the_new_card_and_retires_the_unpriced_one(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await run(client, runner, seen("ps-b", "bag", "đỏ", ts + 2000))
    [live] = await hear(client, store, runner, said("sp-1", "túi này 550 nghìn", ts + 3000))
    assert (live["card_id"], live["status"], live["price"]) == ("card-b", "live", 550000)
    assert await prices(store, "card-a") == []


async def test_fast_change_after_a_live_card_leaves_its_price_alone(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(2))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts + 1000))
    await run(client, runner, seen("ps-b", "bag", "đỏ", ts + 3000))
    [live] = await hear(client, store, runner, said("sp-2", "túi này 550 nghìn", ts + 5000))
    assert (live["card_id"], live["price"]) == ("card-b", 550000)
    assert await prices(store, "card-a") == [450000]


async def test_price_said_before_any_card_writes_nothing(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, [])
    ts = now_ms()
    assert await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts)) == []
    [draft] = await hear(client, store, runner, seen("ps-a", "sneaker", "trắng", ts + 2000))
    assert (draft["status"], draft["price"]) == ("draft", None)


async def test_late_first_price_still_makes_the_card_live(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    [live] = await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts + 11000))
    assert (live["card_id"], live["status"], live["price"]) == ("card-a", "live", 450000)


async def test_late_repeat_of_the_same_price_adds_no_version(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(2))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts + 1000))
    assert await hear(client, store, runner, said("sp-2", "đôi này vẫn 450 nghìn", ts + 17000)) == []
    assert await prices(store, "card-a") == [450000]


async def test_stock_count_is_not_a_price(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(3))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    assert await hear(client, store, runner, said("sp-1", "còn 2 đôi nha", ts + 1000)) == []
    await hear(client, store, runner, said("sp-2", "đôi này 450 nghìn", ts + 2000))
    assert await hear(client, store, runner, said("sp-3", "còn 2 đôi nha", ts + 3000)) == []
    assert await prices(store, "card-a") == [450000]


async def test_next_price_before_its_product_seen_does_not_reprice_the_live_card(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(2))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts + 1000))
    # the next product's price is stored while its frame is still being read, so card-a is current
    await hear(client, store, runner, said("sp-2", "túi này 550 nghìn", ts + 20000))
    await run(client, runner, seen("ps-b", "bag", "đỏ", ts + 22000))
    await settle()
    assert await prices(store, "card-a") == [450000]


async def test_clear_new_price_for_the_live_card_waits_for_the_shop(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(2))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await hear(client, store, runner, said("sp-1", "đôi này 250 nghìn", ts + 1000))
    await hear(client, store, runner, said("sp-2", "đôi này 220 nghìn", ts + 15000))
    assert await prices(store, "card-a") == [250000]


async def test_seller_edited_price_is_not_repriced_by_speech(mem_app, caplog):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    [draft] = await hear(client, store, runner, seen("ps-a", "sneaker", "trắng", ts))
    edit = {k: draft[k] for k in card.CARD_FIELDS} | {"version": 2, "price": 399000, "status": "edited"}
    await run(client, runner, make_event("card", SID, ts + 100, "replay", event_id="console-1", **edit))
    with caplog.at_level(logging.INFO, logger="worker.card"):
        assert await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts + 200)) == []
    assert await prices(store, "card-a") == [399000]
    [line] = [r.getMessage() for r in caplog.records if "price proposal" in r.getMessage()]
    assert line == "price proposal: card card-a v2 price 399000 heard 450000 from sp-1"
    [p] = [e for e in await store.read(SID, 0, 500) if e["type"] == "price_proposal"]
    assert (p["card_version"], p["current_price"], p["heard_price"]) == (2, 399000, 450000)
    assert (p["speech_event_id"], p["caused_by"], p["ts_ms"]) == ("sp-1", ["sp-1"], ts + 200)


async def test_speech_with_size_and_a_new_price_changes_only_the_sizes(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(2))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts + 1000))
    [nxt] = await hear(client, store, runner, said("sp-2", "đôi này 500 nghìn size 38 nhé", ts + 3000))
    assert (nxt["status"], nxt["price"]) == ("live", 450000)
    assert [v["size"] for v in nxt["variants"]] == ["38"]
