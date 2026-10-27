"""worker/brain/card.py: which card a spoken price lands on when products change. Synthetic events only.

Authority: a price is attached only when the card is certain; when it is not, nothing is written; a card that
already has a price is not repriced by speech (a new price is a proposal for the shop). The two xfail(strict)
tests encode that last rule and fail on the current code, which applies a speech to whatever card is current
when it is handled:
- the next product's price arrives before its product_seen, so it reprices the previous live card;
- a clear new price for the live card replaces the price straight away.
Speech goes through the rule reader (the model reply is an error), so the wording is what the card reads.
"""

import pytest

from app.contracts import now_ms
from meta.transport import Reply
from tests.test_card import cards_in, fresh, hear, run, runner_with, said, seen, settle  # noqa: F401

LIVE_REPRICED = "speech still reprices a live card; fix pending its own task spec and reviewer"


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
    [draft] = await run(client, runner, seen("ps-a", "sneaker", "trắng", ts + 2000))
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


@pytest.mark.xfail(strict=True, reason=LIVE_REPRICED)
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


@pytest.mark.xfail(strict=True, reason=LIVE_REPRICED)
async def test_clear_new_price_for_the_live_card_waits_for_the_shop(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(2))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await hear(client, store, runner, said("sp-1", "đôi này 250 nghìn", ts + 1000))
    await hear(client, store, runner, said("sp-2", "đôi này 220 nghìn", ts + 15000))
    assert await prices(store, "card-a") == [250000]
