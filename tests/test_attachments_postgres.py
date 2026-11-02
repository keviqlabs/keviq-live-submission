"""PostgreSQL transaction, epoch-fence, and restart behavior over synthetic histories."""

import asyncio

import httpx
import pytest

from app.attachment_auth import SOURCE_KINDS, WAIT_MS, binding_definition
from app.contracts import make_event
from app.main import create_app
from app.store import PgStore
from tests.test_attachment_evidence_bounds import (
    test_full_stored_evidence_covers_admitted_prefix_without_truncation as full_proof,
)
from tests.test_attachments_runtime import World, two_cards
from tests.test_slow_vision_integration import pg_app  # noqa: F401


@pytest.fixture
async def pg_world(pg_app, monkeypatch):  # noqa: F811
    store, client, _ = pg_app
    value = World(client.app, store, client)
    for name in ("app.store.now_ms", "app.cards_api.now_ms", "app.orders_api.now_ms"):
        monkeypatch.setattr(name, lambda: value.now)
    value.service.clock = lambda: value.now
    await value.configure()
    yield value


async def test_full_proof_is_validated_and_persisted_in_postgres(pg_world):
    await full_proof(pg_world, 1755, True)
    summary = (await pg_world.store.attachments(pg_world.sid))[0]
    assert "readiness_evidence" not in summary
    full = await pg_world.store.attachment_version(pg_world.sid, summary["attachment_id"], 1)
    assert len(full["readiness_evidence"]["handled_frames"]) == 1755


async def test_two_app_writers_return_one_canonical_decision_and_order(pg_world):
    w = pg_world
    _, _, _, body = await two_cards(w)
    other = PgStore(w.store.url)
    await other.open()
    app = create_app(other)
    app.state.store, app.state.attachments.clock = other, lambda: w.now
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first, second = await asyncio.gather(
                w.system_request(body),
                client.post(
                    f"/api/sessions/{w.sid}/attachments/system", json=body, headers=w.headers(w.system)
                ),
            )
            assert second.status_code == 200, second.text
            assert first["attachment"] == second.json()["attachment"]
        events = await w.events()
        assert len([e for e in events if e["type"] == "comment_attachment"]) == 1
        assert len([e for e in events if e["type"] == "order" and e["state"] == "DRAFT"]) == 1
        assert (
            len([e for e in events if e["type"] == "message_out" and e["template"] == "confirm_order"]) == 1
        )
    finally:
        await app.state.attachments.close()
        await other.close()


async def test_observer_reads_committed_history_while_a_writer_is_paused(pg_world, monkeypatch):
    w = pg_world
    _, _, _, body = await two_cards(w)
    entered, release = asyncio.Event(), asyncio.Event()
    append_event = w.store._append

    async def pause(event):
        if event["type"] == "comment_attachment":
            entered.set()
            await release.wait()
        return await append_event(event)

    monkeypatch.setattr(w.store, "_append", pause)
    writer = asyncio.create_task(w.system_request(body))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        rows = await asyncio.wait_for(w.store.read(w.sid, 0, 2000), 0.2)
        assert not [e for e in rows if e["type"] in {"comment_attachment", "order", "message_out"}]
        release.set()
        assert (await writer)["attachment"]["outcome"] == "ready"
    finally:
        release.set()
        await asyncio.gather(writer, return_exceptions=True)


async def test_registration_cannot_race_a_legacy_ingest_into_unauthenticated_source_authority(
    pg_world, monkeypatch
):
    w = pg_world
    sid = "new-" + w.sid
    checked, release = asyncio.Event(), asyncio.Event()
    ingest = w.service.ingest

    async def pause(event, header, **kwargs):
        result = await ingest(event, header, **kwargs)
        if event["session_id"] == sid and result is None:
            checked.set()
            await release.wait()
        return result

    monkeypatch.setattr(w.service, "ingest", pause)
    other = PgStore(w.store.url)
    await other.open()
    app = create_app(other)
    app.state.store, app.state.attachments.clock = other, lambda: w.now
    event = make_event("frame", sid, 10, "replay", jpeg_path="synthetic/frame.jpg", width=10, height=10)
    incoming = asyncio.create_task(w.client.post(f"/api/sessions/{sid}/events", json=event))
    registration = None
    try:
        await asyncio.wait_for(checked.wait(), 2)
        definition = binding_definition(
            sid,
            "epoch",
            "new-system-" + sid,
            [{"token": "new-source-" + sid, "principal": "frame-source", "kinds": {"frame": ["replay"]}}],
        )
        registration = asyncio.create_task(app.state.attachments.configure(definition))
        await asyncio.sleep(0.01)
        assert not registration.done()
        release.set()
        assert (await incoming).status_code == 200
        await registration
        assert not await other.source_receipts(sid)
        rejected = await w.client.post(
            f"/api/sessions/{sid}/events", json={**event, "event_id": "later-" + sid, "ts_ms": 20}
        )
        assert rejected.status_code == 401
    finally:
        release.set()
        await asyncio.gather(*(t for t in (incoming, registration) if t is not None), return_exceptions=True)
        await app.state.attachments.close()
        await other.close()


async def test_failed_order_projection_rolls_back_attachment_and_all_effects(pg_world, monkeypatch):
    w = pg_world
    _, _, _, body = await two_cards(w)
    batch = w.store.append_order_batch

    async def fail_order(events, **kwargs):
        await batch(events[:1], **kwargs)
        raise RuntimeError("synthetic projection interruption")

    monkeypatch.setattr(w.store, "append_order_batch", fail_order)
    with pytest.raises(RuntimeError, match="synthetic projection interruption"):
        await w.system_request(body)
    assert not await w.store.attachments(w.sid)
    assert not await w.store.orders(w.sid)
    assert not [e for e in await w.events() if e["type"] in {"comment_attachment", "order", "message_out"}]
    monkeypatch.setattr(w.store, "append_order_batch", batch)
    await w.system_request(body)
    assert len(await w.store.orders(w.sid)) == 1


async def test_conflicting_comment_reservation_rolls_back_the_whole_continuation(pg_world):
    w = pg_world
    _, _, _, body = await two_cards(w)
    comment = next(e for e in await w.events() if e["event_id"] == body["comment_event_id"])
    with pytest.raises(ValueError, match="comment already belongs"):
        async with w.store.session_transaction(w.sid):
            await w.store.reserve_attachment_order(w.sid, comment["comment_id"], "already-reserved-order")
            await w.system_request(body)
    assert not await w.store.attachments(w.sid)
    assert not await w.store.orders(w.sid)
    assert not [e for e in await w.events() if e["type"] == "message_out"]


async def test_expired_candidate_batch_failure_rolls_back_then_retries_in_causal_order(pg_world, monkeypatch):
    w = pg_world
    _, _, _, body = await two_cards(w)
    await w.service.close()
    comment = next(e for e in await w.events() if e["event_id"] == body["comment_event_id"])
    w.now = comment["ingested_ms"] + WAIT_MS
    batch = w.store.append_order_batch

    async def interrupted(events, **kwargs):
        assert kwargs["attachment"]["outcome"] == "expired"
        await batch(events, **kwargs)
        raise RuntimeError("synthetic expiry commit interruption")

    monkeypatch.setattr(w.store, "append_order_batch", interrupted)
    with pytest.raises(RuntimeError, match="expiry commit interruption"):
        await w.service.expire_due(w.sid)
    assert not await w.store.attachments(w.sid)
    assert not await w.store.orders(w.sid)
    monkeypatch.setattr(w.store, "append_order_batch", batch)
    await w.service.expire_due(w.sid)
    await w.service.expire_due(w.sid)
    effects = [e for e in await w.events() if e["type"] in {"comment_attachment", "order", "message_out"}]
    assert [e.get("state", e["type"]) for e in effects] == [
        "comment_attachment",
        "DRAFT",
        "NEEDS_SELLER",
        "message_out",
    ]
    assert effects[0]["event_id"] in effects[1]["caused_by"]
    assert effects[1]["event_id"] in effects[2]["caused_by"]
    assert all(e["ts_ms"] >= w.now for e in effects)


async def test_restart_recovers_selected_identity_without_repricing_or_duplicate_order(pg_world):
    w = pg_world
    a, b, _, body = await two_cards(w, priced=False)
    initial = (await w.system_request(body))["attachment"]
    assert initial["selected_card_id"] == b["card_id"]
    await w.service.close()
    other = PgStore(w.store.url)
    await other.open()
    app = create_app(other)
    app.state.store, app.state.attachments.clock = other, lambda: w.now
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            before = await client.post(
                f"/api/sessions/{w.sid}/attachments/system", json=body, headers=w.headers(w.system)
            )
            assert before.status_code == 200 and before.json()["attachment"] == initial
            await w.price(a, 150000)
            await app.state.attachments.expire_due(w.sid)
            assert not await other.orders(w.sid)
            await w.price(b, 200000)
            await app.state.attachments.expire_due(w.sid)
            await app.state.attachments.expire_due(w.sid)
            events = await w.events()
            drafts = [e for e in events if e["type"] == "order" and e["state"] == "DRAFT"]
            assert len(drafts) == 1 and drafts[0]["lines"][0]["card_id"] == b["card_id"]
            assert drafts[0]["lines"][0]["unit_price"] == 200000
    finally:
        await app.state.attachments.close()
        await other.close()


async def test_epoch_takeover_serializes_with_commit_and_revokes_old_capability(pg_world, monkeypatch):
    w = pg_world
    _, _, _, body = await two_cards(w)
    entered, release = asyncio.Event(), asyncio.Event()
    append_event = w.store._append

    async def pause(event):
        if event["type"] == "comment_attachment":
            entered.set()
            await release.wait()
        return await append_event(event)

    monkeypatch.setattr(w.store, "_append", pause)
    other = PgStore(w.store.url)
    await other.open()
    app = create_app(other)
    app.state.store, app.state.attachments.clock = other, lambda: w.now
    accepted, rotation = None, None
    try:
        accepted = asyncio.create_task(w.system_request(body))
        await asyncio.wait_for(entered.wait(), 2)
        definition = binding_definition(
            w.sid,
            "epoch-2",
            "new-system-" + w.sid,
            [
                {
                    "token": "new-source-" + w.sid,
                    "principal": "synthetic-source",
                    "kinds": {kind: ["sim", "replay"] for kind in SOURCE_KINDS},
                }
            ],
        )
        rotation = asyncio.create_task(app.state.attachments.configure(definition))
        await asyncio.sleep(0.01)
        assert not rotation.done()
        release.set()
        canonical = (await accepted)["attachment"]
        await rotation
        assert canonical["worker_epoch"] == "epoch-1"
        await w.system_request(body, status=403)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/sessions/{w.sid}/attachments/system",
                json={**body, "worker_epoch": "epoch-2"},
                headers=w.headers("new-system-" + w.sid),
            )
            assert response.status_code == 200, response.text
            assert response.json()["attachment"] == canonical
    finally:
        release.set()
        await asyncio.gather(*(t for t in (accepted, rotation) if t is not None), return_exceptions=True)
        await app.state.attachments.close()
        await other.close()
