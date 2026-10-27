"""worker/brain/card.py with window.py: a heard price goes to the one card whose media span overlaps the line and
fits the kind of item the line names. Any other line leaves the price null for the seller, with a `price wait` log
line (D-025, scenario 6). Synthetic text only. The model reply is an error unless a test says otherwise, so the
rule reader reads the line.
"""

import asyncio
import logging

from app.contracts import now_ms
from meta.transport import responses_reply
from tests.test_card import SID, cards_in, fresh, hear, run, runner_with, said, seen, settle  # noqa: F401
from tests.test_card_handoff import model_down, prices
from worker.brain import card, window
from worker.voice import vn


def line(event_id: str, text: str, start: int, end: int) -> dict:
    return {**said(event_id, text, end), "start_ms": start}


def waits(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("price wait")]


async def latest(store) -> dict[str, dict]:
    return {c["card_id"]: c for c in await cards_in(store)}


async def proposals(store) -> list[dict]:
    return [e for e in await store.read(SID, 0, 500) if e["type"] == "price_proposal"]


def test_magnitude_and_kind_words():
    cats = {"card-a": "sneaker", "card-b": "bag"}
    assert window.choose("đôi này 450k", cats) == (["card-a"], None)
    assert window.choose("đôi này giá 1tr2", cats) == (["card-a"], None)
    assert window.choose("còn 2 đôi nha", cats) == (["card-a"], "no_magnitude")
    assert window.choose("giá 450 nghìn", cats) == (["card-a", "card-b"], "no_kind")
    assert window.choose("đôi giày với túi 450 nghìn", cats) == ([], "none")
    assert window.choose("áo này 300 nghìn", {"card-a": "shirt", "card-b": "jacket"})[1] == "many"
    assert window.choose("tay cầm 300 nghìn", {"card-a": "other"}) == (["card-a"], None)


async def test_next_products_price_waits_for_its_product_seen_within_the_hold(mem_app, monkeypatch):
    _, store, client = mem_app
    runner = runner_with(client, model_down(2))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts + 1000))
    monkeypatch.setattr(window, "HOLD_MS", 300)
    # the bag is in view from ts + 18000; its product_seen arrives after the line, within the hold
    await run(client, runner, line("sp-2", "túi này 550 nghìn", ts + 20000, ts + 21000))
    await run(client, runner, seen("ps-b", "bag", "đỏ", ts + 18000))
    await settle()
    now = await latest(store)
    assert (now["card-b"]["status"], now["card-b"]["price"], now["card-b"]["caused_by"][0]) == (
        "live",
        550000,
        "sp-2",
    )
    assert await prices(store, "card-a") == [450000] and await proposals(store) == []


async def test_a_line_that_starts_after_the_next_frame_skips_the_previous_card(mem_app, monkeypatch):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    monkeypatch.setattr(window, "HOLD_MS", 300)
    # the boot is in view from ts + 5000; the line starts 2.6 s later and arrives before the boot's product_seen
    await run(client, runner, line("sp-1", "đôi này 550 nghìn", ts + 7600, ts + 8400))
    await run(client, runner, seen("ps-b", "boot", "đen", ts + 5000))
    await settle()
    now = await latest(store)
    assert (now["card-b"]["status"], now["card-b"]["price"]) == ("live", 550000)
    assert (now["card-a"]["status"], now["card-a"]["price"]) == ("retired", None)


async def test_a_line_across_a_switch_between_two_shoes_waits(mem_app, caplog):
    _, store, client = mem_app
    runner = runner_with(client, model_down(2))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await hear(client, store, runner, said("sp-1", "đôi này 450 nghìn", ts + 1000))
    await run(client, runner, seen("ps-b", "boot", "đen", ts + 6000))
    with caplog.at_level(logging.INFO, logger="worker.card"):
        assert (
            await hear(client, store, runner, line("sp-2", "đôi này 550 nghìn", ts + 5500, ts + 6500)) == []
        )
    assert waits(caplog) == ["price wait: sp-2 reason many candidates card-a,card-b"]
    assert await prices(store, "card-a") == [450000] and await prices(store, "card-b") == []
    assert await proposals(store) == []


async def test_a_price_read_from_a_line_without_a_magnitude_word_waits(mem_app, caplog):
    _, store, client = mem_app
    runner = runner_with(client, [responses_reply('{"price_vnd": 450000, "sizes": [], "colors": []}')])
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    with caplog.at_level(logging.INFO, logger="worker.card"):
        assert await hear(client, store, runner, said("sp-1", "đôi này 450 nha", ts + 1000)) == []
    assert waits(caplog) == ["price wait: sp-1 reason no_magnitude candidates card-a"]
    assert await prices(store, "card-a") == []


async def test_a_price_without_a_kind_word_waits_and_the_card_stays_unpriced(mem_app, caplog):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    with caplog.at_level(logging.INFO, logger="worker.card"):
        assert await hear(client, store, runner, said("sp-1", "giá 450 nghìn nha", ts + 1000)) == []
    assert waits(caplog) == ["price wait: sp-1 reason no_kind candidates card-a"]
    assert (await latest(store))["card-a"]["price"] is None


async def test_a_price_for_another_kind_of_item_waits(mem_app, caplog):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    with caplog.at_level(logging.INFO, logger="worker.card"):
        assert await hear(client, store, runner, said("sp-1", "túi này 550 nghìn", ts + 1000)) == []
    assert waits(caplog) == ["price wait: sp-1 reason none candidates card-a"]
    assert await prices(store, "card-a") == []


async def test_a_draft_under_a_pending_line_is_retired_once_the_line_is_decided(mem_app, monkeypatch):
    _, store, client = mem_app
    runner = runner_with(client, [])
    gate = asyncio.Event()

    async def slow_read(text, *_):
        await gate.wait()
        return vn.parse(text, set(card.COLORS)), (0, 0)

    monkeypatch.setattr(card, "read_speech", slow_read)
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await run(client, runner, line("sp-1", "túi này 550 nghìn", ts + 800, ts + 1600))
    await run(client, runner, seen("ps-b", "bag", "đỏ", ts + 1500))
    # sp-1 overlaps card-a's span, so the switch does not retire it yet
    assert [c["status"] for c in await cards_in(store) if c["card_id"] == "card-a"] == ["draft"]
    gate.set()
    await settle()
    now = await latest(store)
    assert (now["card-b"]["status"], now["card-b"]["price"]) == ("live", 550000)
    assert (now["card-a"]["status"], now["card-a"]["price"], now["card-a"]["caused_by"][0]) == (
        "retired",
        None,
        "sp-1",
    )


async def test_the_price_said_first_wins_when_its_read_is_slower(mem_app, monkeypatch):
    _, store, client = mem_app
    runner = runner_with(client, [])
    gate = asyncio.Event()

    async def read(text, *_):
        if "450" in text:
            await gate.wait()
        return vn.parse(text, set(card.COLORS)), (0, 0)

    monkeypatch.setattr(card, "read_speech", read)
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    await run(client, runner, said("sp-1", "đôi này 450 nghìn", ts + 1000))
    await run(client, runner, said("sp-2", "đôi này 500 nghìn", ts + 2000))
    await asyncio.sleep(0.05)  # sp-2's read is done
    gate.set()
    await settle()
    assert await prices(store, "card-a") == [450000]
    [p] = await proposals(store)
    assert (p["heard_price"], p["speech_event_id"]) == (500000, "sp-2")


async def test_a_card_seen_after_the_hold_does_not_get_the_waiting_price(mem_app, monkeypatch, caplog):
    _, store, client = mem_app
    runner = runner_with(client, model_down(1))
    ts = now_ms()
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts))
    monkeypatch.setattr(window, "HOLD_MS", 100)
    with caplog.at_level(logging.INFO, logger="worker.card"):
        assert (
            await hear(client, store, runner, line("sp-1", "túi này 550 nghìn", ts + 3000, ts + 3500)) == []
        )
    await hear(client, store, runner, seen("ps-b", "bag", "đỏ", ts + 2500))
    assert waits(caplog) == ["price wait: sp-1 reason none candidates card-a"]
    now = await latest(store)
    assert (now["card-b"]["status"], now["card-b"]["price"]) == ("draft", None)
    assert (now["card-a"]["status"], now["card-a"]["price"]) == ("retired", None)
