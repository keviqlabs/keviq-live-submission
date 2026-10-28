"""Offline HTTP invariants for server-owned attachment authority and continuations."""

import asyncio
import copy
import json
import uuid

import pytest

from app.attachment_auth import SOURCE_KINDS, binding_definition
from app.contracts import make_event


class World:
    def __init__(self, app, store, client):
        self.app, self.store, self.client = app, store, client
        self.sid = "pytest-attachment-" + uuid.uuid4().hex[:10]
        self.now = 100_000
        self.epoch = "epoch-1"
        self.source, self.system = "synthetic-source-" + self.sid, "synthetic-system-" + self.sid
        self.frames, self.cards, self.seen = [], [], []

    @property
    def service(self):
        return self.app.state.attachments

    def headers(self, token=None):
        return {"Authorization": "Bearer " + (token or self.source)}

    async def configure(self, *, ordered=True):
        await self.service.configure(
            binding_definition(
                self.sid,
                self.epoch,
                self.system,
                [
                    {
                        "token": self.source,
                        "principal": "synthetic-source",
                        "kinds": {kind: ["sim", "replay"] for kind in SOURCE_KINDS},
                    }
                ],
                ordered_media=ordered,
            )
        )

    async def ingest(self, event, *, status=200, headers=None):
        response = await self.client.post(
            f"/api/sessions/{self.sid}/events",
            json=event,
            headers=self.headers() if headers is None else headers,
        )
        assert response.status_code == status, response.text
        return response

    async def frame(self, ts):
        event = make_event(
            "frame", self.sid, ts, "replay", jpeg_path="synthetic/frame.jpg", width=10, height=10
        )
        await self.ingest(event)
        self.frames.append(event)
        return event

    async def card(self, ts, sku, price=None):
        frame = await self.frame(ts)
        seen = make_event(
            "product_seen",
            self.sid,
            ts,
            "replay",
            category="sneaker",
            attributes={},
            confidence=1,
            is_new=True,
            frame_ts_ms=ts,
            caused_by=[frame["event_id"]],
        )
        await self.ingest(seen)
        event = make_event(
            "card",
            self.sid,
            self.now,
            "replay",
            card_id="card-" + str(len(self.cards)),
            version=1,
            sku=sku,
            name="Synthetic item",
            price=None,
            currency="VND",
            status="draft",
            stock=5,
            variants=[{"size": "38", "color": "trắng", "stock": 5}],
            image_url=None,
            provenance={"frame_ts_ms": ts, "speech_event_ids": [], "edited_by": None},
            caused_by=[seen["event_id"]],
        )
        await self.ingest(event)
        self.cards.append(event)
        self.seen.append(seen)
        if price is not None:
            await self.price(event, price)
        return event

    async def price(self, card, price):
        latest = max(
            (c for c in await self.store.card_versions(self.sid) if c["card_id"] == card["card_id"]),
            key=lambda c: c["version"],
        )
        response = await self.client.post(
            f"/api/sessions/{self.sid}/cards/{card['card_id']}/price",
            json={"card_version": latest["version"], "price": price},
        )
        assert response.status_code == 200, response.text

    async def comment(self, ts=25, kind="current_card", sku=None, referenced=None, cid="intent"):
        comment = make_event(
            "comment",
            self.sid,
            ts,
            "sim",
            comment_id=cid,
            user_ref="synthetic-buyer",
            text="chốt đôi này size 38",
            reply_to=None,
        )
        await self.ingest(comment)
        label = make_event(
            "comment_labeled",
            self.sid,
            ts,
            "sim",
            comment_id=cid,
            label="order",
            order_draft={
                "sku": sku,
                "variant": {"size": "38", "color": None},
                "qty": 1,
                "reference_kind": kind,
                "referenced_card_id": referenced,
            },
            scam_signals=[],
            action="none",
            latency_ms=0,
            caused_by=[comment["event_id"]],
        )
        await self.ingest(label)
        return comment, label

    async def proof(self, pair):
        comment, label = pair
        return {
            "worker_epoch": self.epoch,
            "comment_event_id": comment["event_id"],
            "label_event_id": label["event_id"],
            "observed_cursor": await self.store.head_seq(),
            "readiness_evidence": {
                "after_frame_event_id": self.frames[-1]["event_id"],
                "handled_frames": [
                    {
                        "frame_event_id": frame["event_id"],
                        "result": "product_seen",
                        "product_seen_event_id": seen["event_id"],
                        "card_event_ids": [card["event_id"]],
                    }
                    for frame, seen, card in zip(self.frames, self.seen, self.cards, strict=False)
                    if frame["ts_ms"] <= comment["ts_ms"]
                ],
            },
        }

    async def system_request(self, body, *, token=None, status=None):
        response = await self.client.post(
            f"/api/sessions/{self.sid}/attachments/system",
            json=body,
            headers=self.headers(token or self.system),
        )
        assert response.status_code in ({200, 202} if status is None else {status}), response.text
        return response.json()

    async def events(self):
        return await self.store.read(self.sid, 0, 5000)

    async def decide(self, attachment, decision, *, expected=None, card_id=None, status=200):
        body = {
            "attachment_version": attachment["version"] if expected is None else expected,
            "decision": decision,
        }
        if card_id is not None:
            body["card_id"] = card_id
        response = await self.client.post(
            f"/api/sessions/{self.sid}/attachments/{attachment['attachment_id']}/decision", json=body
        )
        assert response.status_code == status, response.text
        return response.json()


@pytest.fixture
async def world(mem_app, monkeypatch):
    app, store, client = mem_app
    value = World(app, store, client)
    monkeypatch.setattr("app.store.now_ms", lambda: value.now)
    monkeypatch.setattr("app.cards_api.now_ms", lambda: value.now)
    monkeypatch.setattr("app.orders_api.now_ms", lambda: value.now)
    value.service.clock = lambda: value.now
    await value.configure()
    try:
        yield value
    finally:
        await value.service.close()


async def two_cards(world, kind="current_card", sku=None, priced=True):
    a = await world.card(10, "GIAY-01", 150000 if priced else None)
    b = await world.card(20, "BOOT-02", 200000 if priced else None)
    pair = await world.comment(kind=kind, sku=sku, referenced=a["card_id"])
    await world.frame(30)
    return a, b, pair, await world.proof(pair)


@pytest.mark.parametrize(
    "kind,sku,index", [("current_card", None, 1), ("previous_card", None, 0), ("explicit_sku", "GIAY-01", 0)]
)
async def test_reference_resolution_overrides_stale_label_id(world, kind, sku, index):
    a, b, _, body = await two_cards(world, kind, sku)
    result = await world.system_request(body)
    target = (a, b)[index]
    assert result["attachment"]["outcome"] == "ready"
    assert result["attachment"]["selected_card_id"] == target["card_id"]
    events = await world.events()
    orders = [e for e in events if e["type"] == "order"]
    assert orders[-1]["state"] == "CONFIRM_SENT"
    assert all(
        line["card_id"] == target["card_id"] and line["card_version"] == 2
        for order in orders
        for line in order["lines"]
    )
    assert [e for e in events if e["type"] == "comment_labeled"][-1]["order_draft"][
        "referenced_card_id"
    ] == target["card_id"]


@pytest.mark.parametrize("kind,sku", [("unresolved", None), ("explicit_sku", "AO-10")])
async def test_coverage_does_not_resolve_unknown_reference(world, kind, sku):
    _, _, _, body = await two_cards(world, kind, sku)
    result = await world.system_request(body)
    assert result["pending"] is True
    assert not [e for e in await world.events() if e["type"] in {"order", "comment_attachment"}]


@pytest.mark.parametrize("kind", ["current_card", "explicit_sku"])
async def test_production_labeler_context_and_http_guard_share_the_frame_snapshot(world, kind):
    from types import SimpleNamespace

    from worker.brain.labels import Labeler, Pending
    from worker.runner import Ctx, Runner

    w = world
    old = await w.card(10, "GIAY-01", 150000)
    previous = await w.card(20, "BOOT-02", 200000)
    current = await w.card(30, "TUI-03", 300000)
    if kind == "current_card":
        latest = max(
            (e for e in await w.events() if e["type"] == "card" and e["card_id"] == current["card_id"]),
            key=lambda e: e["version"],
        )
        retired = {k: v for k, v in latest.items() if k not in {"seq", "ingested_ms"}}
        retired.update(
            event_id="retired-" + w.sid, version=3, status="retired", caused_by=[latest["event_id"]]
        )
        await w.ingest(retired)
    comment = make_event(
        "comment",
        w.sid,
        35,
        "sim",
        comment_id="production-labeler",
        user_ref="synthetic-buyer",
        text="synthetic reference",
        reply_to=None,
    )
    await w.ingest(comment)
    await w.frame(40)
    context = []

    class Classifier:
        async def responses(self, prompt, *args, **kwargs):
            import json

            context.append(json.loads(prompt[-1]["content"]))
            return SimpleNamespace(
                data={
                    "items": [
                        {
                            "comment_id": comment["comment_id"],
                            "label": "order",
                            "reference_kind": kind,
                            "sku": "GIAY-01" if kind == "explicit_sku" else None,
                            "size": "38",
                            "color": None,
                            "qty": 1,
                            "contact": "none",
                        }
                    ]
                },
                infer_start_ms=w.now,
                infer_end_ms=w.now,
            )

    runner = Runner(
        "http://test",
        {},
        http=w.client,
        attachment_auth={w.sid: {"epoch": w.epoch, "source_token": w.source, "system_token": w.system}},
    )
    labeler = Labeler(client_for=lambda ctx: Classifier())
    pending = Pending(comment, Ctx(comment, runner, received_ms=w.now), [], "none")
    try:
        versions = await w.store.card_versions(w.sid)
        items, infer = await labeler._ask([pending], versions)
        assert context[0]["comments"][0]["current_card"]["card_id"] == current["card_id"]
        assert context[0]["comments"][0]["previous_card"]["card_id"] == previous["card_id"]
        emitted = await labeler._consequences(pending, items[comment["comment_id"]], versions, infer)
        assert len(emitted) == 1 and emitted[0]["type"] == "comment_labeled"
        assert emitted[0]["order_draft"]["referenced_card_id"] is None
        await runner.post(emitted)
        result = await w.system_request(await w.proof((comment, emitted[0])))
        if kind == "current_card":
            assert result["attachment"]["outcome"] == "ready"
            assert result["attachment"]["selected_card_id"] == current["card_id"]
            assert old["card_id"] != result["attachment"]["selected_card_id"]
        else:
            assert result["pending"] is True  # A real older SKU outside the pair grants no target.
            assert not [e for e in await w.events() if e["type"] == "comment_attachment"]
        assert not [e for e in await w.events() if e["type"] in {"order", "message_out"}]
    finally:
        await runner.evidence.close()


@pytest.mark.parametrize(
    "damage", ["missing_lower", "missing_card", "wrong_frame", "future_cursor", "old_output_epoch"]
)
async def test_incomplete_or_unaccepted_proof_never_becomes_ready(world, damage):
    _, _, _, body = await two_cards(world)
    records = body["readiness_evidence"]["handled_frames"]
    status = 202
    if damage == "missing_lower":
        records.pop(0)
    elif damage == "missing_card":
        records[0]["card_event_ids"] = [world.cards[1]["event_id"]]
    elif damage == "wrong_frame":
        records[0]["product_seen_event_id"] = world.seen[1]["event_id"]
        status = 422
    elif damage == "future_cursor":
        body["observed_cursor"] += 100
        status = 422
    else:
        world.epoch, world.source, world.system = "epoch-2", "synthetic-source-2", "synthetic-system-2"
        await world.configure()
        body["worker_epoch"] = world.epoch
        status = 422
    result = await world.system_request(body, status=status)
    if status == 202:
        assert result["pending"] is True
    assert not [e for e in await world.events() if e["type"] in {"order", "comment_attachment"}]


@pytest.mark.parametrize("token,status", [(None, 401), ("invalid", 401), ("source", 403)])
async def test_system_authentication_is_separate_from_source_authority(world, token, status):
    _, _, _, body = await two_cards(world)
    headers = {} if token is None else world.headers(world.source if token == "source" else token)
    response = await world.client.post(
        f"/api/sessions/{world.sid}/attachments/system", json=body, headers=headers
    )
    assert response.status_code == status
    assert not [e for e in await world.events() if e["type"] == "comment_attachment"]


async def test_system_capability_cannot_forge_generic_source_or_seller_action(world):
    card = await world.card(10, "GIAY-01")
    response = await world.ingest(
        make_event("frame", world.sid, 20, "replay", jpeg_path="synthetic/a.jpg", width=1, height=1),
        headers=world.headers(world.system),
        status=403,
    )
    assert response.status_code == 403
    for route, body in [
        (f"cards/{card['card_id']}/price", {"card_version": 1, "price": 150000}),
        (f"cards/{card['card_id']}/item", {"card_version": 1, "sku": "GIAY-01"}),
        ("orders/unknown/decision", {"decision": "approve"}),
    ]:
        response = await world.client.post(
            f"/api/sessions/{world.sid}/{route}", json=body, headers=world.headers(world.system)
        )
        assert response.status_code == 403, response.text


async def test_unconfigured_system_ingress_cannot_register_itself(mem_app):
    app, store, client = mem_app
    try:
        response = await client.post(
            "/api/sessions/unregistered/attachments/system",
            json={
                "worker_epoch": "claimed-epoch",
                "comment_event_id": "comment",
                "label_event_id": "label",
                "observed_cursor": 0,
            },
            headers={"Authorization": "Bearer synthetic-unregistered"},
        )
        assert response.status_code == 503
        assert await store.head_seq() == 0
    finally:
        await app.state.attachments.close()


async def test_source_capability_is_bound_to_kind_and_accepted_transport(world):
    _, _, pair, _ = await two_cards(world)
    definition = binding_definition(
        world.sid,
        world.epoch,
        world.system,
        [
            {"token": world.source, "principal": "frame-source", "kinds": {"frame": ["replay"]}},
            {
                "token": "synthetic-label-source-" + world.sid,
                "principal": "label-source",
                "kinds": {"comment_labeled": ["sim"]},
            },
        ],
    )
    await world.service.configure(definition)
    label = {**pair[1], "event_id": "forbidden-label-" + world.sid}
    before = await world.store.head_seq()
    await world.ingest(label, status=403)
    assert await world.store.head_seq() == before
    await world.frame(40)


async def test_old_capability_cannot_be_reassigned_to_a_later_epoch(world):
    old_source, old_system = world.source, world.system
    world.epoch, world.source, world.system = "epoch-2", "source-two-" + world.sid, "system-two-" + world.sid
    await world.configure()
    world.epoch, world.source, world.system = "epoch-3", old_source, old_system
    with pytest.raises(ValueError, match="historical epoch"):
        await world.configure()
    assert (await world.store.attachment_binding(world.sid))["epoch"] == "epoch-2"


async def test_cross_session_credential_and_evidence_cannot_supply_a_target(world):
    _, _, _, body = await two_cards(world)
    other = World(world.app, world.store, world.client)
    await other.configure()
    await other.card(5, "GIAY-01")
    body["readiness_evidence"]["handled_frames"][0]["product_seen_event_id"] = other.seen[0]["event_id"]
    await world.system_request(body, status=422)
    response = await world.client.post(
        f"/api/sessions/{other.sid}/attachments/system", json=body, headers=world.headers(world.system)
    )
    assert response.status_code == 403
    assert not await world.store.attachments(world.sid)


async def test_recognition_cannot_name_an_unadmitted_frame(world):
    event = make_event(
        "product_seen",
        world.sid,
        10,
        "replay",
        category="sneaker",
        attributes={},
        confidence=1,
        is_new=True,
        frame_ts_ms=10,
        caused_by=["invented-frame"],
    )
    await world.ingest(event, status=422)
    assert await world.store.head_seq() == 0


@pytest.mark.parametrize("change,status", [("price", 409), ("appearance", 422)])
async def test_source_card_fields_cannot_invent_price_authority_or_appearance(world, change, status):
    card = await world.card(10, "GIAY-01")
    event = {**card, "event_id": "forged-card-" + world.sid, "version": 2}
    if change == "price":
        event.update(price=150000, status="live", provenance={**event["provenance"], "edited_by": "seller"})
    else:
        event["provenance"] = {**event["provenance"], "frame_ts_ms": 20}
    before = await world.store.head_seq()
    await world.ingest(event, status=status)
    assert await world.store.head_seq() == before


async def test_closed_media_prefix_cannot_accept_late_lower_frame(world):
    await world.frame(20)
    before = await world.store.head_seq()
    await world.ingest(
        make_event("frame", world.sid, 10, "replay", jpeg_path="synthetic/a.jpg", width=1, height=1),
        status=409,
    )
    assert await world.store.head_seq() == before


async def test_selected_unpriced_card_waits_and_pins_its_own_price(world):
    a, b, _, body = await two_cards(world, priced=False)
    result = await world.system_request(body)
    selected = result["attachment"]
    assert selected["selected_card_id"] == b["card_id"]
    assert not await world.store.orders(world.sid)
    await world.price(a, 150000)
    await world.service.expire_due(world.sid)
    assert not await world.store.orders(world.sid)
    await world.price(b, 200000)
    await world.service.expire_due(world.sid)
    order = (await world.store.orders(world.sid))[0]
    assert order["state"] == "CONFIRM_SENT"
    assert order["lines"][0]["card_id"] == b["card_id"]
    assert order["lines"][0]["unit_price"] == 200000
    await world.price(b, 210000)
    await world.service.expire_due(world.sid)
    assert (await world.store.orders(world.sid))[0]["lines"][0]["unit_price"] == 200000


async def test_expired_two_priced_candidates_require_explicit_order_choice(world):
    a, b, _, body = await two_cards(world)
    world.now += 4000
    expired = await world.system_request(body)
    assert expired["attachment"]["outcome"] == "expired"
    order = (await world.store.orders(world.sid))[0]
    assert order["state"] == "NEEDS_SELLER"
    assert order["lines"][0]["candidates"] == [b["card_id"], a["card_id"]]
    url = f"/api/sessions/{world.sid}/orders/{order['order_id']}/decision"
    before = await world.store.head_seq()
    for request in ({"decision": "approve"}, {"decision": "approve", "card_id": "foreign-card"}):
        response = await world.client.post(url, json=request)
        assert response.status_code == 422, response.text
    assert await world.store.head_seq() == before
    response = await world.client.post(url, json={"decision": "approve", "card_id": a["card_id"]})
    assert response.status_code == 200, response.text
    selected = (await world.store.orders(world.sid))[0]
    assert selected["state"] == "CONFIRM_SENT" and selected["actor"] == "seller"
    assert selected["lines"][0]["unit_price"] == 150000
    assert selected["lines"][0]["card_id"] == a["card_id"]
    assert selected["lines"][0]["card_version"] == 2
    assert not [e for e in await world.events() if e["type"] == "order" and e["state"] == "CONFIRMED"]


async def test_keep_select_retries_and_preorder_reject_are_audited_without_fake_orders(world):
    a, b, _, body = await two_cards(world, priced=False)
    world.now += 4000
    expired = (await world.system_request(body))["attachment"]
    kept = (await world.decide(expired, "keep_unresolved"))["attachment"]
    assert kept["version"] == 2 and kept["selected_card_id"] is None
    before = await world.store.head_seq()
    retry = await world.decide(expired, "keep_unresolved")
    assert retry["duplicate"] is True and await world.store.head_seq() == before
    await world.decide(expired, "select", card_id=a["card_id"], status=409)
    selected = (await world.decide(kept, "select", card_id=b["card_id"]))["attachment"]
    await world.decide(selected, "select", card_id=a["card_id"], status=409)
    rejected = (await world.decide(selected, "reject"))["attachment"]
    assert rejected["selected_card_id"] == b["card_id"]
    for field in ("candidates", "deadline_ms", "snapshot_at_ms", "snapshot_cursor", "worker_epoch"):
        assert rejected[field] == expired[field]
    assert not [e for e in await world.events() if e["type"] in {"order", "message_out"}]


async def test_timer_expiry_without_worker_request_retains_original_clock_and_null_epoch(world):
    a = await world.card(10, "GIAY-01")
    comment, label = await world.comment(ts=15)
    world.now += 3999
    await world.service.expire_due(world.sid)
    assert not await world.store.attachments(world.sid)
    world.now += 1
    await world.service.expire_due(world.sid)
    event = (await world.store.attachments(world.sid))[0]
    assert event["worker_epoch"] is None and event["deadline_ms"] == 104000
    assert event["ts_ms"] == 104000 and event["comment_event_id"] == comment["event_id"]
    assert event["label_event_id"] == label["event_id"]
    assert event["candidates"][0]["card_id"] == a["card_id"]
    before = await world.store.head_seq()
    await world.service.expire_due(world.sid)
    assert await world.store.head_seq() == before


async def test_late_label_recovery_excludes_late_recognition_and_does_not_reset_deadline(world):
    await world.card(10, "GIAY-01")
    comment = make_event(
        "comment",
        world.sid,
        15,
        "sim",
        comment_id="intent",
        user_ref="synthetic-buyer",
        text="chốt",
        reply_to=None,
    )
    await world.ingest(comment)
    world.now += 5000
    await world.card(14, "BOOT-02")
    label = make_event(
        "comment_labeled",
        world.sid,
        15,
        "sim",
        comment_id="intent",
        label="order",
        order_draft={
            "sku": None,
            "variant": None,
            "qty": 1,
            "reference_kind": "current_card",
            "referenced_card_id": None,
        },
        scam_signals=[],
        action="none",
        latency_ms=5000,
        caused_by=[comment["event_id"]],
    )
    await world.ingest(label)
    await world.service.expire_due(world.sid)
    event = (await world.store.attachments(world.sid))[0]
    assert event["deadline_ms"] == 104000 and event["ts_ms"] == 105000
    assert event["reason"].startswith("recovery-late")
    assert [c["card_id"] for c in event["candidates"]] == [world.cards[0]["card_id"]]


async def test_concurrent_initial_requests_return_one_canonical_history(world):
    _, _, _, body = await two_cards(world)
    first, second = await asyncio.gather(world.system_request(body), world.system_request(body))
    assert first["attachment"] == second["attachment"]
    events = await world.events()
    assert len([e for e in events if e["type"] == "comment_attachment"]) == 1
    assert len([e for e in events if e["type"] == "order" and e["state"] == "DRAFT"]) == 1
    assert len([e for e in events if e["type"] == "message_out" and e["template"] == "confirm_order"]) == 1


async def test_distinct_and_max_length_comment_ids_cannot_share_an_order(world):
    await world.card(10, "GIAY-01", 150000)
    pairs = [
        await world.comment(ts=15, cid=cid)
        for cid in ("cm-same", "same", "same_code", "same-code", "long" * 24)
    ]
    await world.frame(20)
    for pair in pairs:
        await world.system_request(await world.proof(pair))
    events = await world.events()
    drafts = [e for e in events if e["type"] == "order" and e["state"] == "DRAFT"]
    assert len(drafts) == 5 and len({e["order_id"] for e in drafts}) == 5
    assert all(len(e["order_id"]) <= 96 for e in drafts)
    assert all(sum(pair[0]["event_id"] in draft["caused_by"] for draft in drafts) == 1 for pair in pairs)


async def test_generic_ingest_refuses_even_valid_attachment_payload(world):
    _, _, _, body = await two_cards(world)
    canonical = (await world.system_request(body))["attachment"]
    forged = copy.deepcopy(canonical)
    forged["event_id"] = "forged-system-event"
    before = await world.store.head_seq()
    await world.ingest(forged, status=403)
    assert await world.store.head_seq() == before


@pytest.mark.parametrize("collision", ["attachment", "order"])
async def test_occupied_global_event_identity_never_becomes_a_false_canonical_result(world, collision):
    from app.attachments import stable_id
    from orders.flow import step_id

    _, _, pair, body = await two_cards(world)
    attachment = stable_id("attv-", stable_id("att-", world.sid, pair[1]["event_id"]), 1)
    occupied = attachment if collision == "attachment" else step_id(attachment, "o-intent", "draft")
    unrelated = make_event(
        "frame",
        "unregistered-" + world.sid,
        1,
        "replay",
        event_id=occupied,
        jpeg_path="synthetic/frame.jpg",
        width=10,
        height=10,
    )
    response = await world.client.post(f"/api/sessions/{unrelated['session_id']}/events", json=unrelated)
    assert response.status_code == 200, response.text
    body["observed_cursor"] = await world.store.head_seq()
    await world.system_request(body, status=409)
    assert not [
        e for e in await world.events() if e["type"] in {"comment_attachment", "order", "message_out"}
    ]


async def test_raw_body_bound_is_enforced_before_proof_validation(world):
    response = await world.client.post(
        f"/api/sessions/{world.sid}/attachments/system",
        content=json.dumps({"padding": "x" * (1024 * 1024)}),
        headers=world.headers(world.system),
    )
    assert response.status_code == 413


async def test_proof_preparation_crossing_deadline_cannot_write_a_late_ready(world, monkeypatch):
    _, _, _, body = await two_cards(world)
    original = world.store.origin_body
    crossed = False

    def delayed(event, binding, origin=None):
        nonlocal crossed
        if event["type"] == "comment_attachment" and event["action"] == "ready" and not crossed:
            crossed = True
            world.now += 4000
        return original(event, binding, origin)

    monkeypatch.setattr(world.store, "origin_body", delayed)
    result = await world.system_request(body)
    assert result["attachment"]["outcome"] == "expired"
    assert result["attachment"]["ts_ms"] == result["attachment"]["deadline_ms"] == 104000
    assert not [e for e in await world.events() if e["type"] == "order" and e["state"] == "CONFIRM_SENT"]


async def test_sku_null_priced_card_stays_selected_without_an_invented_order(world):
    card = await world.card(10, None, 150000)
    pair = await world.comment(ts=15)
    await world.frame(20)
    result = await world.system_request(await world.proof(pair))
    assert result["attachment"]["selected_card_id"] == card["card_id"]
    assert not await world.store.orders(world.sid)
    response = await world.client.post(
        f"/api/sessions/{world.sid}/cards/{card['card_id']}/item", json={"card_version": 2, "sku": "GIAY-01"}
    )
    assert response.status_code == 409
    kept = await world.decide(result["attachment"], "keep_unresolved")
    assert kept["attachment"]["selected_card_id"] == card["card_id"]
    assert not await world.store.orders(world.sid)


async def test_empty_frozen_snapshot_can_only_be_kept_or_rejected(world):
    await world.comment(ts=15)
    world.now += 4000
    await world.service.expire_due(world.sid)
    expired = (await world.store.attachments(world.sid))[0]
    assert expired["candidates"] == []
    await world.decide(expired, "select", card_id="invented-card", status=422)
    kept = (await world.decide(expired, "keep_unresolved"))["attachment"]
    await world.decide(kept, "reject")
    assert not [e for e in await world.events() if e["type"] in {"order", "message_out"}]


async def test_message_before_confirmation_is_never_replayed_as_a_later_yes(world):
    card = await world.card(10, "GIAY-01")
    pair = await world.comment(ts=15)
    await world.frame(20)
    early = make_event(
        "message_in",
        world.sid,
        world.now,
        "sim",
        thread_ref="th-synthetic-buyer",
        template="confirm_yes",
        payload={"order_id": "o-intent"},
    )
    await world.ingest(early)
    await world.system_request(await world.proof(pair))
    await world.price(card, 150000)
    await world.service.expire_due(world.sid)
    assert (await world.store.orders(world.sid))[0]["state"] == "CONFIRM_SENT"
    assert not [e for e in await world.events() if e["type"] == "message_out" and e["template"] == "receipt"]
    world.now += 10
    later = make_event(
        "message_in",
        world.sid,
        world.now,
        "sim",
        thread_ref="th-synthetic-buyer",
        template="confirm_yes",
        payload={"order_id": "o-intent"},
    )
    await world.ingest(later)
    await world.service.expire_due(world.sid)
    await world.service.expire_due(world.sid)
    assert (await world.store.orders(world.sid))[0]["state"] == "CONFIRMED"
    assert (
        len([e for e in await world.events() if e["type"] == "message_out" and e["template"] == "receipt"])
        == 1
    )


async def test_old_yes_delivered_after_new_confirmation_is_ignored_but_new_yes_works(world):
    card = await world.card(10, "GIAY-01", 150000)
    pair = await world.comment(ts=15)
    await world.frame(20)
    await world.system_request(await world.proof(pair))
    world.now += 100
    await world.price(card, 170000)
    changed = make_event(
        "message_in",
        world.sid,
        world.now,
        "sim",
        thread_ref="th-synthetic-buyer",
        template="confirm_yes",
        payload={"order_id": "o-intent"},
    )
    await world.ingest(changed)
    await world.service.expire_due(world.sid)
    assert (await world.store.orders(world.sid))[0]["state"] == "NEEDS_SELLER"
    world.now += 100
    response = await world.client.post(
        f"/api/sessions/{world.sid}/orders/o-intent/decision",
        json={"decision": "approve", "unit_price": 170000},
    )
    assert response.status_code == 200, response.text
    world.now += 100
    delayed = make_event(
        "message_in",
        world.sid,
        world.now - 150,
        "sim",
        thread_ref="th-synthetic-buyer",
        template="confirm_yes",
        payload={"order_id": "o-intent"},
    )
    await world.ingest(delayed)
    await world.service.expire_due(world.sid)
    assert (await world.store.orders(world.sid))[0]["state"] == "CONFIRM_SENT"
    world.now += 100
    current = make_event(
        "message_in",
        world.sid,
        world.now,
        "sim",
        thread_ref="th-synthetic-buyer",
        template="confirm_yes",
        payload={"order_id": "o-intent"},
    )
    await world.ingest(current)
    await world.service.expire_due(world.sid)
    assert (await world.store.orders(world.sid))[0]["state"] == "CONFIRMED"


async def test_keep_does_not_repeat_variant_question_and_reply_pins_current_approved_version(world):
    card = await world.card(10, "GIAY-01", 150000)
    latest = max(await world.store.card_versions(world.sid), key=lambda c: c["version"])
    changed = {
        **latest,
        "event_id": "synthetic-two-variants-" + world.sid,
        "version": 3,
        "variants": [
            {"size": "38", "color": "trắng", "stock": 5},
            {"size": "39", "color": "trắng", "stock": 5},
        ],
        "caused_by": [world.seen[0]["event_id"]],
    }
    await world.ingest(changed)
    comment = make_event(
        "comment",
        world.sid,
        15,
        "sim",
        comment_id="intent",
        user_ref="synthetic-buyer",
        text="chốt đôi này",
        reply_to=None,
    )
    await world.ingest(comment)
    label = make_event(
        "comment_labeled",
        world.sid,
        15,
        "sim",
        comment_id="intent",
        label="order",
        order_draft={
            "sku": None,
            "variant": None,
            "qty": 1,
            "reference_kind": "current_card",
            "referenced_card_id": None,
        },
        scam_signals=[],
        action="none",
        latency_ms=0,
        caused_by=[comment["event_id"]],
    )
    await world.ingest(label)
    await world.frame(20)
    body = await world.proof((comment, label))
    body["readiness_evidence"]["handled_frames"][0]["card_event_ids"].append(changed["event_id"])
    initial = (await world.system_request(body))["attachment"]
    await world.decide(initial, "keep_unresolved")
    await world.service.expire_due(world.sid)
    asks = [e for e in await world.events() if e["type"] == "message_out" and e["template"] == "ask_variant"]
    assert len(asks) == 1 and not await world.store.orders(world.sid)
    await world.price(card, 170000)
    reply = make_event(
        "message_in",
        world.sid,
        world.now + 1,
        "sim",
        thread_ref="th-synthetic-buyer",
        template="pick_variant",
        payload={"order_id": "o-intent", "quick_reply": "PICK:o-intent#0"},
    )
    await world.ingest(reply)
    await world.service.expire_due(world.sid)
    order = (await world.store.orders(world.sid))[0]
    assert order["state"] == "CONFIRM_SENT"
    assert order["lines"][0]["card_id"] == card["card_id"]
    assert order["lines"][0]["card_version"] == 4 and order["lines"][0]["unit_price"] == 170000
