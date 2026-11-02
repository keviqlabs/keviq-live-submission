"""A customer's order comment followed by their cancel comment, labeled by two overlapping batches of the
session where the later batch lands first: the customer's labels still post in cut order, so the cancel
question follows the order (unbound through orders/flow, bound through the attachment reconcile), while
another customer's label in the later batch posts at once. A failed batch releases what waited on it.

The fake model answers from the comment text (no model call); a call that holds a chosen comment stays open
until the test releases it, so which batch lands first is decided by the test, not by the clock.
"""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from app.contracts import make_event, now_ms
from connectors.sim.moderator import SimModerator
from meta.client import MetaClient
from meta.meter import Meter, memory_spend
from meta.transport import responses_reply
from orders import flow
from orders import handlers as orders_handlers
from tests.test_attachments_runtime import DEADLINE_MS, world  # noqa: F401  (the bound-session fixture)
from tests.test_labels import card_event, drain, feed, wire
from worker.brain import labels as labels_mod
from worker.brain.labels import Labeler
from worker.runner import Runner, build

ORDER = "chốt đôi này size {size}"
CANCEL = "huỷ đơn giúp mình nha shop"
CHAT = "shop ơi còn hàng không"


def clock() -> float:
    return asyncio.get_running_loop().time()


class GatedTransport:
    """Labels a comment that starts with "chốt" as an order for the current card (size `size`, qty 1), the
    CANCEL comment as `cancel_label` and any other comment as other. A call holding one of `held` sets `opened`
    and stays open until `release` is set. `batches` has the comment ids of each answered call, in landing
    order."""

    def __init__(self, held: set[str], size: str = "40", cancel_label: str = "other"):
        self.held, self.size, self.cancel_label = set(held), size, cancel_label
        self.opened, self.release = asyncio.Event(), asyncio.Event()
        self.batches: list[list[str]] = []

    async def post(self, path: str, body: dict):
        comments = json.loads(body["input"][1]["content"])["comments"]
        ids = [c["comment_id"] for c in comments]
        if self.held & set(ids):
            self.opened.set()
            await self.release.wait()
        self.batches.append(ids)
        items = []
        for c in comments:
            order = c["text"].startswith("chốt")
            items.append(
                {
                    "comment_id": c["comment_id"],
                    "label": "order" if order else self.cancel_label if c["text"] == CANCEL else "other",
                    "reference_kind": "current_card" if order else "none",
                    "sku": None,
                    "size": self.size if order else None,
                    "color": None,
                    "qty": 1 if order else None,
                    "contact": "none",
                }
            )
        return responses_reply(json.dumps({"items": items}, ensure_ascii=False), 900, 300)

    async def aclose(self):
        pass


def comment(sid: str, cid: str, user_ref: str, text: str, ts_ms: int) -> dict:
    return make_event(
        "comment",
        sid,
        ts_ms,
        "sim",
        event_id=cid,
        comment_id=cid,
        user_ref=user_ref,
        text=text,
        reply_to=None,
    )


async def until(predicate, what: str, limit: float = 5.0) -> None:
    deadline = clock() + limit
    while not predicate():
        assert clock() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.01)


async def landed(labeler: Labeler, transport: GatedTransport, sid: str, n: int) -> None:
    """Wait until the transport has answered `n` calls and only the held call's task is still open: the
    batches that landed have posted what they post at once."""
    await until(
        lambda: len(transport.batches) == n and len(labeler.open[sid]) == 1,
        f"{n} batches landed and their tasks done",
    )


async def stored_labels(store, sid: str) -> list[dict]:
    return [e for e in await store.read(sid, 0, 500) if e["type"] == "comment_labeled"]


def quick_replies(event: dict) -> list[str]:
    """The quick replies of a stored message_out (the Send API body the simulated messenger built)."""
    return [q["payload"] for q in event["payload"]["send_api"]["message"]["quick_replies"]]


async def test_a_customers_labels_post_in_cut_order_while_another_customers_post_at_once(
    mem_app, monkeypatch
):
    """Batch 1 (a-1 of customer u-a) is open when batch 2 (a-2 of u-a, b-1 of u-b) is cut and lands first: b-1
    posts at once, a-2 waits for batch 1, then a-1 and a-2 post when batch 1 lands. Waiting labels still count
    as being labeled."""
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.05)
    _, store, client = mem_app
    sid, now = "causal-lane", now_ms()
    transport = GatedTransport({"a-1"})
    labeler, runner = wire(client, transport)
    await feed(client, runner, [(0, comment(sid, "a-1", "u-a", CHAT, now))])
    await asyncio.wait_for(transport.opened.wait(), 5)
    await feed(
        client,
        runner,
        [(0, comment(sid, "a-2", "u-a", CHAT, now + 1)), (0, comment(sid, "b-1", "u-b", CHAT, now + 2))],
    )
    await landed(labeler, transport, sid, 1)
    assert transport.batches == [["a-2", "b-1"]]
    assert [e["comment_id"] for e in await stored_labels(store, sid)] == ["b-1"]
    assert labeler.backlog() == {"waiting": 0, "in_flight": 2}
    transport.release.set()
    await drain(labeler)
    assert transport.batches == [["a-2", "b-1"], ["a-1"]]
    assert [e["comment_id"] for e in await stored_labels(store, sid)] == ["b-1", "a-1", "a-2"]
    assert labeler.backlog() == {"waiting": 0, "in_flight": 0}


@pytest.mark.parametrize(
    "cancel_label", ["other", "complaint"]
)  # the model may call a plain cancel a complaint
async def test_unbound_order_then_cancel_landing_out_of_order_still_asks_to_cancel(
    mem_app, monkeypatch, cancel_label
):
    """Session without an attachment binding: the order comment's call is slow, the cancel comment's call is
    fast. The stored labels, replayed in order through the orders handlers as the worker does, give the order
    (DRAFT, CONFIRM_SENT, confirm_order) and then the cancel question; no order is CONFIRMED."""
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.05)
    _, store, client = mem_app
    sid, now = "causal-unbound", now_ms()
    transport = GatedTransport({"c-order"}, cancel_label=cancel_label)
    labeler, runner = wire(client, transport)
    assert await runner.attachment_enabled(sid) is False
    card = card_event(sid, "card-1", 1, "live", 150_000, now - 60_000, now - 60_000)
    order = comment(sid, "c-order", "u-9", ORDER.format(size="40"), now - 30_000)
    await feed(client, runner, [(0, card), (0, order)])
    await asyncio.wait_for(transport.opened.wait(), 5)
    await feed(client, runner, [(0, comment(sid, "c-cancel", "u-9", CANCEL, now - 20_000))])
    await landed(labeler, transport, sid, 1)
    assert transport.batches == [["c-cancel"]]
    transport.release.set()
    await drain(labeler)
    assert transport.batches == [["c-cancel"], ["c-order"]]
    labels = await stored_labels(store, sid)

    orders = Runner("http://test", orders_handlers.HANDLERS, http=client)
    for label in labels:
        await orders.handle(label)
    assert orders.failures == 0
    events = await store.read(sid, 0, 500)
    oid = flow.order_id_for("c-order")
    messages = [e for e in events if e["type"] == "message_out"]
    assert [m["template"] for m in messages] == ["confirm_order", "ask_cancel"]
    assert quick_replies(messages[1]) == [f"CANCEL:{oid}", f"KEEP:{oid}"]
    states = [(e["order_id"], e["state"]) for e in events if e["type"] == "order"]
    assert states == [(oid, "DRAFT"), (oid, "CONFIRM_SENT")]
    assert [(e["comment_id"], e["label"]) for e in labels] == [
        ("c-order", "order"),
        ("c-cancel", cancel_label),
    ]


class ProvingRunner(Runner):
    """The worker runner of a bound session whose proof of an order label is submitted as soon as that label is
    posted (the evidence task is replaced), so the attachment resolves, and the order exists, before anything
    the lane posts after it is stored."""

    def __init__(self, w, handlers):
        auth = {w.sid: {"epoch": w.epoch, "source_token": w.source, "system_token": w.system}}
        super().__init__("http://test", handlers, http=w.client, attachment_auth=auth)
        self.w, self.proofs = w, []
        self.evidence = SimpleNamespace(posted=lambda events, accepted=None: None)

    async def post(self, events: list[dict]) -> None:
        await super().post(events)
        for label in events:
            if label["type"] == "comment_labeled" and label["label"] == "order":
                stored = await self.w.events()
                source = next(
                    e for e in stored if e["type"] == "comment" and e["comment_id"] == label["comment_id"]
                )
                self.proofs.append(await self.w.system_request(await self.w.proof((source, label))))


@pytest.mark.parametrize("cancel_label", ["other", "complaint"])
async def test_bound_order_then_cancel_landing_out_of_order_still_asks_to_cancel(
    world,  # noqa: F811
    monkeypatch,
    cancel_label,
):
    """Session with an attachment binding: the order comment's call is slow, the cancel comment's call is fast.
    The order label's proof resolves the attachment and drafts the order (CONFIRM_SENT) before the cancel label
    is stored, so the next reconcile pass asks the customer; the cancel input is consumed only then."""
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.05)
    w = world
    await w.card(10, "GIAY-01", 150_000)
    transport = GatedTransport({"c-order"}, size="38", cancel_label=cancel_label)
    holder = {}

    def client_for(ctx):
        async def emit(fields):
            await holder["runner"].post([build(ctx.event, {"type": "model_call", **fields}, now_ms())])

        return MetaClient(transport, Meter(memory_spend([0.0]), 30, 300, 20), emit)

    labeler = Labeler(client_for=client_for, moderator=SimModerator())
    holder["runner"] = runner = ProvingRunner(w, labeler.handlers())
    assert await runner.attachment_enabled(w.sid) is True
    order = comment(w.sid, "c-order", "synthetic-buyer", ORDER.format(size="38"), 25)
    await w.ingest(order)
    await runner.handle(order)
    await asyncio.wait_for(transport.opened.wait(), 5)
    cancel = comment(w.sid, "c-cancel", "synthetic-buyer", CANCEL, 26)
    await w.ingest(cancel)
    await runner.handle(cancel)
    await w.frame(30)
    await landed(labeler, transport, w.sid, 1)
    assert transport.batches == [["c-cancel"]]
    transport.release.set()
    await drain(labeler)
    assert transport.batches == [["c-cancel"], ["c-order"]]
    assert [p["attachment"]["outcome"] for p in runner.proofs] == ["ready"]
    await w.service.expire_due(w.sid)

    events = await w.events()
    labels = {e["comment_id"]: e for e in events if e["type"] == "comment_labeled" and e["source"] == "sim"}
    orders = [e for e in events if e["type"] == "order"]
    assert [e["state"] for e in orders] == ["DRAFT", "CONFIRM_SENT"]
    oid = orders[0]["order_id"]
    asks = [e for e in events if e["type"] == "message_out" and e["template"] == "ask_cancel"]
    assert [quick_replies(a) for a in asks] == [[f"CANCEL:{oid}", f"KEEP:{oid}"]]
    assert orders[0]["seq"] < labels["c-cancel"]["seq"], "the order exists before the cancel label is stored"
    assert labels["c-cancel"]["event_id"] in await w.store.consumed_attachment_inputs(w.sid)


async def test_a_failed_batch_releases_the_customers_later_batch(mem_app, monkeypatch, caplog):
    """Batch 1 (a-1 of u-a) is refused when it posts; batch 2 (a-2 of u-a), which waited for it, posts anyway
    and nothing stays counted as being labeled."""
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.05)
    _, store, client = mem_app
    sid, now = "causal-failed", now_ms()
    transport = GatedTransport({"a-1"})
    labeler, runner = wire(client, transport)
    post = runner.post

    async def refusing_post(events):
        if any(e["type"] == "comment_labeled" and e["comment_id"] == "a-1" for e in events):
            raise RuntimeError("post refused")
        await post(events)

    monkeypatch.setattr(runner, "post", refusing_post)
    await feed(client, runner, [(0, comment(sid, "a-1", "u-a", CHAT, now))])
    await asyncio.wait_for(transport.opened.wait(), 5)
    await feed(client, runner, [(0, comment(sid, "a-2", "u-a", CHAT, now + 1))])
    await landed(labeler, transport, sid, 1)
    transport.release.set()
    with caplog.at_level(logging.ERROR, logger="worker.labels"):
        await drain(labeler)
    assert transport.batches == [["a-2"], ["a-1"]]
    assert [e["comment_id"] for e in await stored_labels(store, sid)] == ["a-2"]
    assert labeler.backlog() == {"waiting": 0, "in_flight": 0}
    assert [
        r for r in caplog.records if "batch of 1 for causal-failed failed: post refused" in r.getMessage()
    ]


async def test_bound_cancel_label_waits_for_the_order_of_a_delayed_proof(world, monkeypatch):  # noqa: F811
    """Session with an attachment binding, through the worker's real evidence path: the card comes from the
    worker's read of a frame, the order comment's call is slow and the cancel comment's call is fast. The labels
    post in cut order; the evidence task then proves the order label, and the proof reaches the app 20 ms after
    it is sent, so the cancel label is stored before the order it asks about exists. The customer is still asked
    after the order is confirmed: the cancel input waits for the earlier comment's attachment instead of being
    consumed against an empty order list."""
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.05)
    w = world
    transport = GatedTransport({"c-order"}, size="38")
    holder = {}

    def client_for(ctx):
        async def emit(fields):
            await holder["runner"].post([build(ctx.event, {"type": "model_call", **fields}, now_ms())])

        return MetaClient(transport, Meter(memory_spend([0.0]), 30, 300, 20), emit)

    async def read(frame, ctx):
        if frame["ts_ms"] != 10:
            return []
        return [{"type": "product_seen", "category": "sneaker", "attributes": {}, "confidence": 1, "is_new": True,
                 "frame_ts_ms": frame["ts_ms"]}]  # fmt: skip

    async def open_card(seen, ctx):
        return [{"type": "card", "ts_ms": w.now, "card_id": "card-worker", "version": 1, "sku": "GIAY-01",
                 "name": "Synthetic item", "price": None, "currency": "VND", "status": "draft", "stock": 5,
                 "variants": [{"size": "38", "color": "trắng", "stock": 5}], "image_url": None,
                 "provenance": {"frame_ts_ms": seen["frame_ts_ms"], "speech_event_ids": [], "edited_by": None}}]  # fmt: skip

    labeler = Labeler(client_for=client_for, moderator=SimModerator())
    handlers = {**labeler.handlers(), "frame": [read], "product_seen": [open_card]}
    auth = {w.sid: {"epoch": w.epoch, "source_token": w.source, "system_token": w.system}}
    holder["runner"] = runner = Runner("http://test", handlers, http=w.client, attachment_auth=auth)
    post = runner.post

    async def echoed(events):  # the stream hands the worker's own posts back to it
        await post(events)
        for event in events:
            runner.receive(event)

    runner.post = echoed
    client_post = w.client.post

    async def delayed_post(url, *args, **kwargs):  # the proof reaches the app 20 ms after the worker sends it
        if str(url).endswith("/attachments/system"):
            await asyncio.sleep(0.02)
        return await client_post(url, *args, **kwargs)

    monkeypatch.setattr(w.client, "post", delayed_post)
    try:
        runner.receive(await w.frame(10))
        stored = []
        for _ in range(300):  # the read, the card and their posts: the card stored, both lanes idle
            stored = await w.events()
            if any(e["type"] == "card" for e in stored) and all(
                runner.stats()[k]["in_flight"] == runner.stats()[k]["waiting"] == 0
                for k in ("fast", "vision")
            ):
                break
            await asyncio.sleep(0.01)
        assert [e["card_id"] for e in stored if e["type"] == "card"] == ["card-worker"]
        await w.price({"card_id": "card-worker"}, 150_000)
        order = comment(w.sid, "c-order", "synthetic-buyer", ORDER.format(size="38"), 25)
        await w.ingest(order)
        runner.receive(order)
        await asyncio.wait_for(transport.opened.wait(), 5)
        cancel = comment(w.sid, "c-cancel", "synthetic-buyer", CANCEL, 26)
        await w.ingest(cancel)
        runner.receive(cancel)
        runner.receive(await w.frame(30))
        await landed(labeler, transport, w.sid, 1)
        assert transport.batches == [["c-cancel"]]
        transport.release.set()
        await drain(labeler)
        assert transport.batches == [["c-cancel"], ["c-order"]]
        await until(lambda: (w.sid, "c-order") in runner.evidence.closed, "the proof of the order label")
        await w.service.expire_due(w.sid)  # the projector's next pass
    finally:
        await runner.evidence.close()

    events = await w.events()
    labels = {e["comment_id"]: e for e in events if e["type"] == "comment_labeled" and e["source"] == "sim"}
    assert [a["outcome"] for a in events if a["type"] == "comment_attachment"] == ["ready"]
    orders = [e for e in events if e["type"] == "order"]
    assert [e["state"] for e in orders] == ["DRAFT", "CONFIRM_SENT"]
    oid = orders[0]["order_id"]
    messages = [e for e in events if e["type"] == "message_out"]
    assert [m["template"] for m in messages] == ["confirm_order", "ask_cancel"]
    assert quick_replies(messages[1]) == [f"CANCEL:{oid}", f"KEEP:{oid}"]
    assert labels["c-cancel"]["seq"] < orders[0]["seq"], "the cancel label is stored before the order exists"
    assert labels["c-cancel"]["event_id"] in await w.store.consumed_attachment_inputs(w.sid)


async def test_bound_cancel_label_is_judged_once_the_earlier_attachment_expires_without_an_order(world):  # noqa: F811
    """Session with an attachment binding: the customer's order comment is labeled and its attachment is still
    open when their cancel comment's label is stored. The cancel input is not consumed while that attachment can
    still produce an order; when it expires with one candidate (no order), the next pass judges the cancel input
    against the orders that exist (none), sends nothing and consumes it."""
    w = world
    await w.card(10, "GIAY-01", 150_000)
    await w.comment(ts=25, cid="c-order")
    await w.frame(30)
    cancel = comment(w.sid, "c-cancel", "synthetic-buyer", CANCEL, 26)
    await w.ingest(cancel)
    cancel_label = make_event(
        "comment_labeled",
        w.sid,
        26,
        "sim",
        comment_id="c-cancel",
        label="other",
        order_draft=None,
        scam_signals=[],
        action="none",
        latency_ms=0,
        caused_by=[cancel["event_id"]],
    )
    await w.ingest(cancel_label)
    await w.service.expire_due(w.sid)
    assert cancel_label["event_id"] not in await w.store.consumed_attachment_inputs(w.sid), (
        "the cancel input waits for the earlier comment's attachment"
    )
    kinds = {"comment_attachment", "order", "message_out"}
    assert [e["type"] for e in await w.events() if e["type"] in kinds] == []
    w.now = DEADLINE_MS
    await w.service.expire_due(w.sid)
    events = await w.events()
    attachments = [e for e in events if e["type"] == "comment_attachment"]
    assert [(a["outcome"], a["selected_card_id"]) for a in attachments] == [("expired", None)]
    assert [e["type"] for e in events if e["type"] in {"order", "message_out"}] == []
    assert cancel_label["event_id"] in await w.store.consumed_attachment_inputs(w.sid)
