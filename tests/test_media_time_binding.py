"""Four timelines for rule 1 of docs/specs/current-item-and-stock-rules.md.

Through the app/runner seam; intentionally red until media-time binding exists.
"""

import asyncio
import importlib
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.attachment_auth import SOURCE_KINDS, binding_definition
from app.contracts import make_event
from orders import handlers as order_handlers
from tests.test_slow_vision_integration import NoModel, frame, ingest, pg_app, running  # noqa: F401
from worker import runner as runner_mod
from worker.brain import card, labels, window

SPEED = 10


class Clock:
    def __init__(self, start):
        self.started = time.monotonic()
        self.first = start
        self.epoch = time.time_ns() // 1_000_000 - round(start * 1000)

    def seconds(self):
        return self.first + (time.monotonic() - self.started) * SPEED

    def at(self, seconds):
        return self.epoch + round(seconds * 1000)

    def now_ms(self):
        return self.at(self.seconds())

    async def until(self, seconds):
        await asyncio.sleep(max(0, seconds - self.seconds()) / SPEED)


class CardTimers:
    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, seconds):
        await asyncio.sleep(seconds / SPEED)


class Classifier:
    def __init__(self, clock):
        self.clock = clock

    async def responses(self, prompt, *args, **kwargs):
        comments = json.loads(prompt[-1]["content"])["comments"]
        items = [{"comment_id": c["comment_id"], "label": "order", "reference_kind": "current_card",
                  "sku": None, "size": "38", "color": None, "qty": 1, "contact": "none"} for c in comments]  # fmt: skip
        return SimpleNamespace(
            data={"items": items}, infer_start_ms=self.clock.now_ms(), infer_end_ms=self.clock.now_ms()
        )


class Scenario:
    def __init__(self, store, client, sid, clock, plans):
        self.store, self.client, self.sid, self.clock, self.plans = store, client, sid, clock, plans
        self.blocked_frames = {}
        self.finished_frames = defaultdict(asyncio.Event)
        self.frame_outcomes = {}

    async def frame(self, number, category=None, color=None, ready=None):
        self.plans[number] = (category, color, number if ready is None else ready)
        await self.clock.until(number)
        await ingest(self.client, frame(self.sid, number, self.clock.at(number)))

    async def seen(self, source, category, color, suffix):
        return await ingest(self.client, make_event("product_seen", self.sid, self.clock.at(source), "sim",
            event_id=f"{self.sid}.seen-{suffix}", category=category, attributes={"color": color}, confidence=1.0,
            is_new=True, frame_ts_ms=self.clock.at(source), caused_by=[f"{self.sid}.fr-{source}"]))  # fmt: skip

    async def frame_done(self, number):
        await asyncio.wait_for(self.finished_frames[number].wait(), timeout=2)

    async def events(self):
        return await self.store.read(self.sid, 0, 2000)

    async def latest(self, sku):
        until = time.monotonic() + 2
        while True:
            cards = [e for e in await self.events() if e["type"] == "card" and e["sku"] == sku]
            if cards:
                return max(cards, key=lambda c: c["version"])
            assert time.monotonic() < until, f"fixture did not open {sku}"
            await asyncio.sleep(0.005)

    async def price(self, sku, amount):
        current = await self.latest(sku)
        response = await self.client.post(
            f"/api/sessions/{self.sid}/cards/{current['card_id']}/price",
            json={"card_version": current["version"], "price": amount},
        )
        assert response.status_code == 200, response.text
        return await self.latest(sku)

    async def comment(self, at, cid):
        await self.clock.until(at)
        await ingest(self.client, make_event("comment", self.sid, self.clock.at(at), "sim", comment_id=cid,
            user_ref="synthetic-buyer", text="chốt đôi này size 38", reply_to=None))  # fmt: skip

    async def speech(self, arrives, start, end, text):
        await self.clock.until(arrives)
        await ingest(self.client, make_event("speech", self.sid, self.clock.at(end), "sim", text=text, final=True,
            lang="vi", start_ms=self.clock.at(start), end_ms=self.clock.at(end)))  # fmt: skip

    async def settle(self):
        await asyncio.sleep(0.06)

    async def snapshot(self):
        events = await self.events()
        frames = {e["event_id"]: e for e in events if e["type"] == "frame"}
        return {
            "session": self.sid,
            "input_frames": [
                {
                    "event_id": e["event_id"],
                    "ts_ms": e["ts_ms"],
                    "ingested_ms": e["ingested_ms"],
                    "outcome": self.frame_outcomes.get(e["event_id"]),
                }
                for e in frames.values()
            ],
            "input_comments": [
                {k: e[k] for k in ("event_id", "comment_id", "ts_ms", "ingested_ms")}
                for e in events
                if e["type"] == "comment"
            ],
            "recognition_sources": [
                {
                    "event_id": e["event_id"],
                    "frame_ts_ms": e["frame_ts_ms"],
                    "ingested_ms": e["ingested_ms"],
                    "frame_refs": [
                        {
                            "event_id": ref,
                            "exists": ref in frames,
                            "ts_ms": frames[ref]["ts_ms"] if ref in frames else None,
                        }
                        for ref in e["caused_by"]
                    ],
                }
                for e in events
                if e["type"] == "product_seen"
            ],
            "cards": [
                {
                    k: e.get(k)
                    for k in (
                        "event_id",
                        "card_id",
                        "version",
                        "sku",
                        "price",
                        "status",
                        "ts_ms",
                        "provenance",
                    )
                }
                for e in events
                if e["type"] == "card"
            ],
            "labels": [
                {"comment_id": e["comment_id"], "draft": e["order_draft"]}
                for e in events
                if e["type"] == "comment_labeled"
            ],
            "orders": [
                {k: e.get(k) for k in ("order_id", "state", "lines", "caused_by", "ts_ms")}
                for e in events
                if e["type"] == "order"
            ],
            "messages": [e["template"] for e in events if e["type"] == "message_out"],
            "model_calls": sum(e["type"] == "model_call" for e in events),
        }


def write_report(row):
    if dest := os.environ.get("MEDIA_BINDING_REPORT"):
        with Path(dest).open("a") as out:
            out.write(json.dumps(row, sort_keys=True) + "\n")


@pytest.fixture
async def scenario(pg_app, monkeypatch, request):  # noqa: F811
    store, client, sid = pg_app
    clock = Clock(
        5 if "price_before" in request.node.name else 10 if "expiry" not in request.node.name else 15
    )
    for name in (
        "app.contracts",
        "app.store",
        "app.main",
        "app.cards_api",
        "app.orders_api",
        "worker.runner",
        "worker.brain.card",
        "worker.brain.labels",
    ):
        module = importlib.import_module(name)
        monkeypatch.setattr(module, "now_ms", clock.now_ms)
    monkeypatch.setattr(card, "asyncio", CardTimers())
    monkeypatch.setattr(labels, "asyncio", CardTimers())
    monkeypatch.setattr(labels, "ATTACH_WAIT_MS", 4000, raising=False)
    monkeypatch.setattr(card, "SESSIONS", {})
    monkeypatch.setattr(window, "HOLD_MS", 4168)
    monkeypatch.setattr(runner_mod.Runner, "client_for", lambda self, event: NoModel())
    credentials = {"epoch": "epoch-" + sid, "source_token": "source-" + sid, "system_token": "system-" + sid}
    client.source_headers = {"Authorization": "Bearer " + credentials["source_token"]}
    client.app.state.attachments.sleep_scale = SPEED
    await client.app.state.attachments.configure(
        binding_definition(
            sid,
            credentials["epoch"],
            credentials["system_token"],
            [
                {
                    "token": credentials["source_token"],
                    "principal": "synthetic-source",
                    "kinds": {kind: ["sim", "replay"] for kind in SOURCE_KINDS},
                }
            ],
        )
    )
    labeler = labels.Labeler(client_for=lambda ctx: Classifier(clock))
    monkeypatch.setitem(runner_mod.BACKLOGS, "labels", labeler.backlog)
    plans = {}

    async def recognize(event, ctx):
        number = int(event["event_id"].rsplit("-", 1)[1])
        category, color, ready = plans[number]
        if number in value.blocked_frames:
            started, release, completed = value.blocked_frames[number]
            started.set()
            await release.wait()
            completed.set()
        await clock.until(ready)
        if category is None:
            return []
        return [
            {
                "type": "product_seen",
                "category": category,
                "attributes": {"color": color},
                "confidence": 1.0,
                "is_new": True,
                "frame_ts_ms": event["ts_ms"],
            }
        ]

    handlers = defaultdict(list)
    for group in (card.HANDLERS, labeler.handlers(), order_handlers.HANDLERS):
        for kind, fns in group.items():
            handlers[kind].extend(fns)
    handlers["frame"] = [recognize]
    value = Scenario(store, client, sid, clock, plans)
    release_frame = runner_mod.Runner.release

    def released(self, lane, event, outcome):
        release_frame(self, lane, event, outcome)
        if event["type"] == "frame":
            number = int(event["event_id"].rsplit("-", 1)[1])
            value.frame_outcomes[event["event_id"]] = {"kind": outcome, "at_ms": clock.now_ms()}
            value.finished_frames[number].set()

    monkeypatch.setattr(runner_mod.Runner, "release", released)
    try:
        async with running(
            client, dict(handlers), sid, monkeypatch, speed=SPEED, attachment_auth={sid: credentials}
        ):
            yield value
    finally:
        snapshot = await value.snapshot()
        print("MEDIA_BINDING " + json.dumps({"case": request.node.name, **snapshot}, sort_keys=True))
        write_report({"case": request.node.name, **snapshot})
        jobs = [*labeler.tasks, *(t for s in card.SESSIONS.values() for t in s.tasks)]
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        assert snapshot["model_calls"] == 0


async def test_switch_before_result_waits_for_new_items_price(scenario):
    s = scenario
    await s.frame(10, "sneaker", "trắng")
    a = await s.price("GIAY-01", 150000)
    await s.frame(12, "boot", "đen", ready=16)
    await s.comment(12.5, "switch-order")
    await s.frame(13)
    await s.clock.until(17.5)
    before_price = await s.events()
    await s.clock.until(18)
    b = await s.price("BOOT-02", 200000)
    await s.settle()
    events = await s.events()
    assert not [e for e in before_price if e["type"] == "order"], "order was priced before B had approval"
    orders = [e for e in events if e["type"] == "order"]
    assert orders and orders[-1]["state"] == "CONFIRM_SENT"
    assert all(
        line["card_id"] == b["card_id"]
        and line["unit_price"] == 200000
        and line["card_version"] == b["version"]
        for e in orders
        for line in e["lines"]
    )
    assert all(a["card_id"] != line["card_id"] for e in orders for line in e["lines"])


async def test_price_before_card_pins_later_price_without_backwriting(scenario):
    s = scenario
    await s.frame(5, "tshirt", "đen")
    p = await s.price("AO-10", 120000)
    p_history = [e for e in await s.events() if e["type"] == "card" and e["card_id"] == p["card_id"]]
    assert any(e["version"] == 1 and e["price"] is None and e["status"] == "draft" for e in p_history)
    assert (
        p in p_history
        and p["price"] == 120000
        and p["status"] == "edited"
        and p["provenance"]["edited_by"] == "seller"
    )
    await s.speech(10, 9, 9.5, "giày này 150 nghìn")
    await s.frame(10, "sneaker", "trắng", ready=13.8)
    await s.comment(11, "early-price-order")
    await s.frame(11)
    await s.frame_done(11)
    await s.frame(14)
    await s.clock.until(16)
    await s.settle()
    a = await s.latest("GIAY-01")
    events = await s.events()
    orders = [e for e in events if e["type"] == "order"]
    assert a["price"] == 150000, "the held price did not reach the newly opened sneaker card"
    assert orders and orders[-1]["state"] == "CONFIRM_SENT", (
        "order did not resume on the later approved price"
    )
    assert all(
        line["card_id"] == a["card_id"]
        and line["unit_price"] == 150000
        and line["card_version"] == a["version"]
        for e in orders
        for line in e["lines"]
    )
    assert [e for e in events if e["type"] == "card" and e["card_id"] == p["card_id"]] == p_history
    assert a["ts_ms"] >= s.clock.at(13.8) and a["ts_ms"] >= a["provenance"]["frame_ts_ms"]


async def test_late_result_and_seller_edit_do_not_restore_previous_item(scenario):
    s = scenario
    await s.frame(10, "sneaker", "trắng")
    a = await s.price("GIAY-01", 150000)
    await s.frame(20, "boot", "đen")
    b = await s.price("BOOT-02", 200000)
    await s.frame(30, "sneaker", "trắng", ready=32.5)
    await s.clock.until(33)
    before = [e for e in await s.events() if e["type"] == "card"]
    await s.seen(20, "boot", "đen", "late-b")
    await s.settle()
    after = [e for e in await s.events() if e["type"] == "card"]
    await s.clock.until(33.8)
    await s.price("BOOT-02", 210000)
    await s.comment(34, "return-order")
    await s.frame(35)
    await s.settle()
    live = (
        await s.client.get(f"/api/sessions/{s.sid}/cards/live", params={"ts_ms": s.clock.at(35.5)})
    ).json()["cards"]
    assert len(after) == len(before), "stale recognition created another card version"
    assert live[0]["card_id"] == a["card_id"], "card creation/edit time overrode the last appearance"
    orders = [e for e in await s.events() if e["type"] == "order"]
    assert orders and all(
        line["card_id"] == a["card_id"] and line["unit_price"] == 150000
        for e in orders
        for line in e["lines"]
    )
    assert b["card_id"] != a["card_id"]


async def test_expiry_needs_seller_with_two_candidates_instead_of_old_price(scenario):
    s = scenario
    await s.frame(15, "sneaker", "trắng")
    a = await s.price("GIAY-01", 150000)
    await s.frame(19, ready=25)  # a lower media frame remains unfinished
    await s.frame(20)
    await s.comment(20.4, "expiry-order")
    await s.frame(21)
    await s.clock.until(23)
    await s.seen(20, "boot", "đen", "b")  # completed recognition delivered before the lower frame's result
    b = await s.price("BOOT-02", 200000)
    await s.clock.until(24.8)
    await s.settle()
    events = await s.events()
    orders = [e for e in events if e["type"] == "order"]
    assert orders and orders[-1]["state"] == "NEEDS_SELLER", (
        "incomplete media evidence was silently confirmed"
    )
    assert orders[-1]["lines"][0].get("candidates") == [b["card_id"], a["card_id"]]
    assert not [
        e
        for e in events
        if e["type"] == "message_out" and e["template"] in ("confirm_order", "ask_which_item")
    ]
    assert all(e["ts_ms"] >= s.clock.at(24.4) for e in orders), "expiry was backdated or bypassed"


async def test_expiry_deadline_while_lower_frame_result_is_blocked(scenario):
    """Seller outcome within 4000 + 200 logical ms, observed before vision is released."""
    s = scenario
    await s.frame(15, "sneaker", "trắng")
    a = await s.price("GIAY-01", 150000)
    started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    s.blocked_frames[19] = (started, release, completed)
    try:
        await s.frame(19)
        await asyncio.wait_for(started.wait(), timeout=0.2)
        await s.frame(20)
        await s.comment(20.4, "deadline-order")
        comment = next(
            e for e in await s.events() if e.get("comment_id") == "deadline-order" and e["type"] == "comment"
        )
        deadline = comment["ingested_ms"] + 4000
        tolerance = 200  # logical milliseconds: 20 ms wall time at SPEED=10
        await s.clock.until(23)
        await s.seen(20, "boot", "đen", "deadline-b")
        b = await s.price("BOOT-02", 200000)
        outcome = None
        observed_at = None
        events = []
        while s.clock.now_ms() <= deadline + tolerance:
            events = await s.events()
            sellers = [
                e
                for e in events
                if e["type"] == "order"
                and e["order_id"] == "o-deadline-order"
                and e["state"] == "NEEDS_SELLER"
            ]
            if sellers:
                outcome, observed_at = sellers[-1], s.clock.now_ms()
                break
            await asyncio.sleep(0.001)
        orders = [e for e in events if e["type"] == "order"]
        print(
            "EXPIRY_DEADLINE "
            + json.dumps(
                {
                    "comment_id": comment["event_id"],
                    "deadline_ms": deadline,
                    "tolerance_ms": tolerance,
                    "observed_at_ms": observed_at,
                    "outcome_id": outcome["event_id"] if outcome else None,
                    "outcome_ingested_ms": outcome["ingested_ms"] if outcome else None,
                    "order_states": [e["state"] for e in orders],
                    "result_released": release.is_set(),
                    "result_completed": completed.is_set(),
                },
                sort_keys=True,
            )
        )
        assert started.is_set() and not release.is_set() and not completed.is_set()
        assert outcome is not None, "seller timeout missing while lower frame result remains blocked"
        assert deadline <= outcome["ingested_ms"] <= deadline + tolerance
        assert deadline <= outcome["ts_ms"] <= deadline + tolerance
        assert observed_at <= deadline + tolerance, "seller outcome observed after deadline tolerance"
        assert outcome["lines"][0].get("candidates") == [b["card_id"], a["card_id"]]
        assert all(e["ingested_ms"] >= deadline for e in orders), "order emitted before expiry"
        assert not [
            e
            for e in events
            if e["type"] == "message_out" and e["template"] in ("confirm_order", "ask_which_item")
        ]
    finally:
        release.set()  # only after observing and asserting the deadline snapshot, including failure
