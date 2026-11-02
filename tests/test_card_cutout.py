"""worker/brain/card.py: a card's SAM cut-out starts when its draft opens, so the version that makes it live carries
the image; at most one SAM call per card, a live card's cut-out ahead of the drafts', and no version posted after a
product_seen's handler returns cites that product_seen (the attachment evidence records them then)."""

import asyncio

from app.contracts import make_event, now_ms
from meta.transport import responses_reply
from orders import flow
from tests.test_card import SID, cards_in, fresh, run, runner_with, said, seen, settle  # noqa: F401
from tests.test_orders_receipt_version import Recorder
from worker.brain import card
from worker.vision import frames, sam
from worker.voice import vn

JPEG = b"\xff\xd8jpeg"


def watch(monkeypatch, runner, gates: dict | None = None) -> list[tuple[str, str]]:
    """SAM stand-in: each call is noted as (category, the event its model_call is caused by) and waits for the
    category's gate, if any. Speech is read by the rules, so the scripted replies are SAM's alone."""
    calls, causes, gates = [], {}, gates or {}
    client_for = runner.client_for

    def tagged(event):
        client = client_for(event)
        causes[id(client)] = event["event_id"]
        return client

    async def cut_out(client, jpeg, category):
        assert jpeg == JPEG
        calls.append((category, causes.get(id(client))))
        if category in gates:
            await gates[category].wait()
        return f"data:image/png;base64,{category}=", (0, 1)

    async def read(text, ctx):
        return vn.Spoken(450000 if "450" in text else None, [], []), (0, 0)

    monkeypatch.setattr(runner, "client_for", tagged)
    monkeypatch.setattr(sam, "cut_out", cut_out)
    monkeypatch.setattr(card, "read_speech", read)
    return calls


async def until(check) -> None:
    """Wait for check() (a value or an awaitable) to be truthy while the session's tasks run."""
    for _ in range(200):
        got = check()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


async def citing(store, event_id: str) -> set[str]:
    return {c["event_id"] for c in await cards_in(store) if event_id in c.get("caused_by", [])}


async def versions_of(store, card_id: str) -> list[dict]:
    return [c for c in await cards_in(store) if c["card_id"] == card_id]


async def test_a_card_that_goes_live_before_its_cut_out_is_done_gets_it_as_the_next_version(
    mem_app, monkeypatch
):
    _, store, client = mem_app
    runner, gate, ts = runner_with(client, []), asyncio.Event(), now_ms()
    calls = watch(monkeypatch, runner, {"sneaker": gate})
    frames.put(SID, ts, JPEG)
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts, caused_by=["fr-a"]))
    await until(lambda: calls == [("sneaker", "ps-a")])
    await run(client, runner, said("sp-1", "đôi này 450 nghìn", ts + 800))

    async def live():
        return [c for c in await cards_in(store) if c["status"] == "live"]

    await until(live)
    [first] = await live()
    assert (first["version"], first["image_url"], first["caused_by"]) == (2, None, ["sp-1"])
    gate.set()
    await settle()
    *_, last = await cards_in(store)
    assert (last["version"], last["status"], last["image_url"]) == (
        3,
        "live",
        "data:image/png;base64,sneaker=",
    )
    assert last["caused_by"] == ["sp-1", "fr-a"]
    assert calls == [("sneaker", "ps-a")]  # the cut-out started at the draft is the one the live card gets
    assert len(await citing(store, "ps-a")) == 1


async def test_a_cut_out_started_at_the_draft_never_posts_a_version_citing_its_product_seen(
    mem_app, monkeypatch
):
    """The prefetch path of the attachment invariant: an unidentified sneaker is read again (a new frame, same
    card), then a bag retires it; SAM answers for the sneaker only after all of that. No version is added to what
    each product_seen cited when its handler returned, and the bag goes live with its own cut-out."""
    _, store, client = mem_app
    runner, gate, ts = runner_with(client, []), asyncio.Event(), now_ms()
    calls = watch(monkeypatch, runner, {"sneaker": gate})
    for t in (ts, ts + 1000, ts + 3000):
        frames.put(SID, t, JPEG)
    recorded = {}
    for event in (
        seen("ps-a", "sneaker", None, ts, caused_by=["fr-a"]),
        seen("ps-a2", "sneaker", None, ts + 1000, caused_by=["fr-a2"]),
        seen("ps-b", "bag", "đỏ", ts + 3000, caused_by=["fr-b"]),
    ):
        await run(client, runner, event)
        recorded[event["event_id"]] = await citing(store, event["event_id"])
        await until(lambda: calls)  # SAM is working on the sneaker before the next frame is read
    assert [len(recorded[k]) for k in ("ps-a", "ps-a2", "ps-b")] == [1, 1, 2]  # ps-b: card-a retired, card-b
    assert calls == [("sneaker", "ps-a")]  # the repeat does not start a second call for the same card
    gate.set()
    await settle()
    assert {k: await citing(store, k) for k in recorded} == recorded
    await run(client, runner, said("sp-1", "cái túi này 450 nghìn", ts + 3500))
    await settle()
    assert {k: await citing(store, k) for k in recorded} == recorded
    *_, live = await versions_of(store, "card-b")
    assert (live["version"], live["status"], live["image_url"]) == (2, "live", "data:image/png;base64,bag=")
    assert live["caused_by"] == ["sp-1", "fr-b"]
    assert [c["image_url"] for c in await versions_of(store, "card-a")] == [None, None, None]
    assert calls == [("sneaker", "ps-a"), ("bag", "ps-b")]


async def test_a_retired_cards_cut_out_does_not_delay_the_live_cards(mem_app, monkeypatch):
    """SAM is busy with A when B and C open; C goes live before its own cut-out started. When SAM is free, C's
    image goes first; B was retired and C's queued cut-out is the one already made, so neither calls SAM."""
    _, store, client = mem_app
    runner, gate, ts = runner_with(client, []), asyncio.Event(), now_ms()
    calls = watch(monkeypatch, runner, {"sneaker": gate})
    for t in (ts, ts + 2000, ts + 4000):
        frames.put(SID, t, JPEG)
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts, caused_by=["fr-a"]))
    await until(lambda: calls)  # SAM is working on A when B and C open
    await run(client, runner, seen("ps-b", "bag", "đỏ", ts + 2000, caused_by=["fr-b"]))
    await run(client, runner, seen("ps-c", "boot", "đen", ts + 4000, caused_by=["fr-c"]))
    await run(client, runner, said("sp-1", "đôi này 450 nghìn", ts + 4500))

    async def live():
        return [c for c in await versions_of(store, "card-c") if c["status"] == "live"]

    s = card.SESSIONS[SID]
    await until(lambda: s.cutouts)
    assert [len(s.prefetches), len(s.cutouts)] == [2, 1]  # B's and C's cut-outs wait behind C's image
    [first] = await live()
    assert (first["version"], first["image_url"]) == (2, None)
    gate.set()
    await settle()
    assert calls == [("sneaker", "ps-a"), ("boot", "sp-1")]
    *_, last = await versions_of(store, "card-c")
    assert (last["version"], last["status"], last["image_url"]) == (3, "live", "data:image/png;base64,boot=")
    assert last["caused_by"] == ["sp-1", "fr-c"]
    assert [c["image_url"] for c in await versions_of(store, "card-b")] == [None, None]
    assert [c["status"] for c in await versions_of(store, "card-a")] == ["draft", "retired"]


async def test_an_order_on_the_first_live_version_quotes_and_receipts_the_cut_out(mem_app, monkeypatch):
    """The customer comments as soon as the card is live; the order is drafted from what is stored then. The
    version it is locked to has the image, so the quote and the CONFIRMED receipt carry it."""
    _, store, client = mem_app
    runner, ts = runner_with(client, []), now_ms()
    watch(monkeypatch, runner)
    frames.put(SID, ts, JPEG)
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts, caused_by=["fr-a"]))
    await settle()
    await run(client, runner, said("sp-1", "đôi này 450 nghìn", ts + 800))
    await settle()
    cards = await cards_in(store)
    first = next(i for i, c in enumerate(cards) if c["status"] == "live")
    known, live = cards[: first + 1], cards[first]
    at = live["ts_ms"] + 1
    comment = {"event_id": "cm-x", "type": "comment", "ts_ms": at, "comment_id": "cm-x", "user_ref": "ux",
               "text": "chốt 39"}  # fmt: skip
    label = {"event_id": "lb-x", "type": "comment_labeled", "ts_ms": at, "comment_id": "cm-x", "label": "order",
             "order_draft": {"variant": {"size": "39", "color": None}, "qty": 1, "referenced_card_id": "card-a"}}  # fmt: skip
    events = [*known, comment, label]
    out = await flow.on_labeled(label, flow.View.of(events), Recorder(), now=at + 1)
    assert [e.get("state") or e["template"] for e in out] == ["DRAFT", "CONFIRM_SENT", "confirm_order"]
    quote = out[2]["payload"]
    assert quote["card_version"] == live["version"] == 2
    assert quote["image_url"] == live["image_url"] == "data:image/png;base64,sneaker="
    ok = {"event_id": "mi-x", "type": "message_in", "ts_ms": at + 2, "thread_ref": "th-ux", "template": None,
          "payload": {"text": "ok"}}  # fmt: skip
    events = [*events, *out, ok]
    out = await flow.on_reply(ok, flow.View.of(events), Recorder(), now=at + 2)
    assert [e.get("state") or e["template"] for e in out] == ["CONFIRMED", "receipt"]
    assert out[0]["receipt"] == {"card_id": "card-a", "card_version": 2, "image_url": live["image_url"],
                                 "price": 450000}  # fmt: skip
    assert out[1]["payload"]["image_url"] == live["image_url"]


async def test_the_cut_out_is_one_model_call_caused_by_the_product_seen(mem_app, monkeypatch):
    """The real SAM call through the client: SAM finds nothing on the draft's frame, the card goes live without an
    image and SAM is not asked again for the same card."""
    _, store, client = mem_app
    runner, ts = runner_with(client, [responses_reply("<0f>0")]), now_ms()
    transport = runner._meta[0]

    async def read(text, ctx):
        return vn.Spoken(450000, [], []), (0, 0)

    monkeypatch.setattr(card, "read_speech", read)
    frames.put(SID, ts, JPEG)
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts, caused_by=["fr-a"]))
    await settle()
    await run(client, runner, said("sp-1", "đôi này 450 nghìn", ts + 800))
    await settle()
    calls = [e for e in await store.read(SID, 0, 100) if e["type"] == "model_call"]
    assert [(c["purpose"], c["caused_by"]) for c in calls] == [("card_cutout", ["ps-a"])]
    assert [r[1]["input"][0]["content"][0]["text"] for r in transport.requests] == ["sneaker"]
    draft, live = await cards_in(store)
    assert (live["status"], live["image_url"]) == ("live", None)


async def test_a_card_the_seller_priced_gets_its_cut_out_as_the_next_version(mem_app, monkeypatch):
    """The seller's price route writes status edited; the cut-out made at the draft follows as the worker's next
    version, caused by the seller's version and the frame."""
    _, store, client = mem_app
    runner, ts = runner_with(client, []), now_ms()
    calls = watch(monkeypatch, runner)
    frames.put(SID, ts, JPEG)
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts, caused_by=["fr-a"]))
    await settle()
    [draft] = await cards_in(store)
    edit = {k: draft[k] for k in card.CARD_FIELDS} | {"version": 2, "price": 399000, "status": "edited"}
    await run(client, runner, make_event("card", SID, ts + 100, "replay", event_id="console-1", **edit))
    await settle()
    *_, last = await cards_in(store)
    assert (last["version"], last["status"], last["price"]) == (3, "edited", 399000)
    assert last["image_url"] == "data:image/png;base64,sneaker=" and last["caused_by"] == [
        "console-1",
        "fr-a",
    ]
    assert calls == [("sneaker", "ps-a")]


async def test_a_card_whose_frame_was_never_cached_calls_no_sam(mem_app, monkeypatch):
    _, store, client = mem_app
    runner, ts = runner_with(client, []), now_ms()
    calls = watch(monkeypatch, runner)
    await run(client, runner, seen("ps-a", "sneaker", "trắng", ts, caused_by=["fr-a"]))
    await run(client, runner, said("sp-1", "đôi này 450 nghìn", ts + 800))
    await settle()
    assert calls == [] and [(c["status"], c["image_url"]) for c in await cards_in(store)] == [
        ("draft", None),
        ("live", None),
    ]


async def test_an_other_card_asks_sam_for_its_kind(mem_app):
    _, store, client = mem_app
    runner, ts = runner_with(client, [responses_reply("<0f>0")]), now_ms()
    frames.put(SID, ts, JPEG)
    attributes = {
        "color": "đen",
        "size": None,
        "material": None,
        "kind": "bàn phím",
        "identity_state": "one_product",
    }
    event = make_event(
        "product_seen", SID, ts, "replay", event_id="ps-k", category="other", attributes=attributes,
        confidence=0.9, is_new=True, frame_ts_ms=ts, caused_by=["fr-k"],
    )  # fmt: skip
    await run(client, runner, event)
    await settle()
    assert [r[1]["input"][0]["content"][0]["text"] for r in runner._meta[0].requests] == ["keyboard"]
