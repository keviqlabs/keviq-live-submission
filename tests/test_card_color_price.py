"""worker/brain/card.py: a heard price is not applied to a card when the line names colours, none of them the
card's (`color_mismatch` price wait). A line that names the card's colour, or names none, is decided as before.
The card's colours are its catalog item's: a card without a SKU gets no colour check, whatever colour vision saw.
Only the card's colours a line can name count, and a line about another colour leaves the card's sizes alone.
Synthetic text only; the model reply is an error, so the rule reader reads the line.
"""

import logging
import unicodedata

from app.contracts import now_ms
from tests.test_card import SID, cards_in, fresh, hear, run, runner_with, said, seen  # noqa: F401
from tests.test_card_handoff import model_down
from worker.brain import card


def waits(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("price wait")]


async def latest(store) -> dict[str, dict]:
    return {c["card_id"]: c for c in await cards_in(store)}


async def proposals(store) -> list[dict]:
    return [e for e in await store.read(SID, 0, 500) if e["type"] == "price_proposal"]


async def test_a_price_said_with_another_colour_waits(mem_app, caplog):
    """The white sneaker (GIAY-01, white only) is on screen; the seller prices a black pair."""
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    with caplog.at_level(logging.INFO, logger="worker.card"):
        assert await hear(client, store, runner, said("sp-1", "đôi đen 450 nghìn", ts + 800)) == []
    assert waits(caplog) == [
        "price wait: sp-1 reason color_mismatch candidates card-a; session cards1 waits color_mismatch=1"
    ]
    assert card.SESSIONS[SID].waits == {"color_mismatch": 1}
    now = (await latest(store))["card-a"]
    assert (now["sku"], now["status"], now["price"]) == ("GIAY-01", "draft", None)


async def test_a_price_said_with_the_cards_colour_is_applied(mem_app, caplog):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    with caplog.at_level(logging.INFO, logger="worker.card"):
        [live] = await hear(client, store, runner, said("sp-1", "đôi trắng 450 nghìn", ts + 800))
    assert (live["card_id"], live["status"], live["price"]) == ("card-a", "live", 450000)
    assert waits(caplog) == [] and not card.SESSIONS[SID].waits


async def test_a_price_said_with_the_cards_colour_among_others_is_applied(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    [live] = await hear(client, store, runner, said("sp-1", "đôi trắng với đen 450 nghìn", ts + 800))
    assert (live["status"], live["price"]) == ("live", 450000)
    assert not card.SESSIONS[SID].waits


async def test_a_price_said_without_a_colour_is_applied(mem_app, caplog):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    with caplog.at_level(logging.INFO, logger="worker.card"):
        [live] = await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts + 800))
    assert (live["status"], live["price"]) == ("live", 450000)
    assert waits(caplog) == [] and not card.SESSIONS[SID].waits


async def test_a_card_with_no_known_colour_takes_the_price(mem_app):
    """Two sneakers match a sneaker of no seen colour: the draft has no SKU and no colour, so there is no check."""
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-x", "sneaker", None, ts))
    [live] = await hear(client, store, runner, said("sp-1", "đôi đen 450 nghìn", ts + 800))
    assert (live["card_id"], live["sku"], live["status"], live["price"]) == ("card-x", None, "live", 450000)
    assert not card.SESSIONS[SID].waits


async def test_a_card_without_sku_takes_a_price_said_with_a_colour_vision_did_not_see(mem_app, caplog):
    """No white boot is in the catalog: the draft has no SKU. Vision read the pair as white, the seller prices it
    as grey; vision can misread a colour, so a card without a SKU gets no colour check and takes the price."""
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-w", "boot", "trắng", ts))
    with caplog.at_level(logging.INFO, logger="worker.card"):
        out = await hear(
            client, store, runner, said("sp-1", "Đôi màu xám giá hai trăm tám mươi nghìn", ts + 800)
        )
    assert waits(caplog) == []
    assert not card.SESSIONS[SID].waits
    [live] = out
    assert (live["card_id"], live["sku"], live["status"], live["price"]) == ("card-w", None, "live", 280000)


async def test_any_colour_of_the_cards_item_takes_the_price(mem_app):
    """CAO-01 comes in black and red; seen in black, a price said for the red pair is the same item's."""
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-h", "heels", "đen", ts))
    [live] = await hear(client, store, runner, said("sp-1", "đôi đỏ 450 nghìn", ts + 800))
    assert (live["sku"], live["status"], live["price"]) == ("CAO-01", "live", 450000)
    assert not card.SESSIONS[SID].waits


async def test_a_priced_card_gets_no_proposal_from_another_colours_price(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(2))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts + 800))
    assert await hear(client, store, runner, said("sp-2", "đôi đen 550 nghìn", ts + 1600)) == []
    assert await proposals(store) == []
    assert card.SESSIONS[SID].waits == {"color_mismatch": 1}
    assert (await latest(store))["card-a"]["price"] == 450000


async def test_a_catalog_colour_in_decomposed_form_is_the_spoken_colour(mem_app, monkeypatch):
    """A seller catalog spells grey in decomposed form; it is still the grey a line names."""
    _, store, client = mem_app
    giay = card.item_by_sku("GIAY-01")
    xam = unicodedata.normalize("NFD", "xám")
    grey = {**giay, "sku": "GIAY-08", "variants": [{"size": "38", "color": xam, "stock": 1}]}
    monkeypatch.setattr(card, "CATALOG", [*card.CATALOG, grey])
    runner = runner_with(client, model_down(2))
    ts = now_ms()
    await run(client, runner, seen("ps-g", "sneaker", xam, ts))
    assert await hear(client, store, runner, said("sp-1", "đôi đen 450 nghìn", ts + 800)) == []
    assert card.SESSIONS[SID].waits == {"color_mismatch": 1}
    [live] = await hear(client, store, runner, said("sp-2", "đôi xám 450 nghìn", ts + 1600))
    assert (live["sku"], live["status"], live["price"]) == ("GIAY-08", "live", 450000)


async def test_a_catalog_colour_no_line_can_name_means_no_colour_check(mem_app, monkeypatch):
    """A seller catalog has a yellow sneaker; yellow is not a colour the speech side reads."""
    _, store, client = mem_app
    giay = card.item_by_sku("GIAY-01")
    yellow = {**giay, "sku": "GIAY-09", "variants": [{"size": "38", "color": "vàng", "stock": 1}]}
    monkeypatch.setattr(card, "CATALOG", [*card.CATALOG, yellow])
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-v", "sneaker", "vàng", ts))
    [live] = await hear(client, store, runner, said("sp-1", "đôi đen 450 nghìn", ts + 800))
    assert (live["sku"], live["status"], live["price"]) == ("GIAY-09", "live", 450000)
    assert not card.SESSIONS[SID].waits


async def test_sizes_said_with_another_colours_price_leave_the_card_as_it_is(mem_app):
    """The white sneaker (GIAY-01: 38, 39, 40) is on screen; the seller prices a black pair and gives its size."""
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    [opened] = await hear(client, store, runner, seen("ps-a", "sneaker", "trắng", ts))
    assert await hear(client, store, runner, said("sp-1", "đôi đen 450 nghìn, còn size 38", ts + 800)) == []
    assert card.SESSIONS[SID].waits == {"color_mismatch": 1}
    now = (await latest(store))["card-a"]
    assert (now["version"], now["status"], now["price"], now["stock"]) == (1, "draft", None, 6)
    assert now["variants"] == opened["variants"]
    assert [v["size"] for v in now["variants"]] == ["38", "39", "40"]


async def test_sizes_said_for_another_colour_without_a_price_leave_the_card_as_it_is(mem_app):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    [opened] = await hear(client, store, runner, seen("ps-a", "sneaker", "trắng", ts))
    assert await hear(client, store, runner, said("sp-1", "đôi đen còn size 38", ts + 800)) == []
    assert not card.SESSIONS[SID].waits  # no price heard, so no price wait
    now = (await latest(store))["card-a"]
    assert (now["version"], now["status"], now["price"], now["stock"]) == (1, "draft", None, 6)
    assert now["variants"] == opened["variants"]
    assert [v["size"] for v in now["variants"]] == ["38", "39", "40"]
