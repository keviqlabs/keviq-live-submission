import asyncio
import copy
import json

import pytest

from app.contracts import make_event
from measure import c5, c5_offline
from tests.test_attachments_runtime import world  # noqa: F401
from worker import runner
from worker.brain import labels
from worker.vision import frames, sam


@pytest.fixture
def installed(monkeypatch, tmp_path):
    for obj, name in [
        (runner.Runner, "client_for"),
        (runner.Runner, "process"),
        (labels.LABELER, "client_for"),
        (frames, "load"),
        (sam, "cut_out"),
    ]:
        monkeypatch.setattr(obj, name, getattr(obj, name))
    path = tmp_path / "labels.json"
    path.write_text(json.dumps({"products": {}}))
    monkeypatch.setattr(c5, "LABELS", path)
    monkeypatch.setattr(c5_offline, "stored_cutouts", lambda: {})
    return c5_offline.install(1, lambda *args, **kwargs: None)


async def chain(w, late_error):
    began, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def no_read(event, ctx):
        began.set()
        if late_error:
            await release.wait()
        try:
            ctx.client().responses([], {})
        except RuntimeError:
            raise runner.Failed("offline no model") from None

    async def open_card(event, ctx):
        calls.append(event["event_id"])
        return [
            {
                "type": "card",
                "card_id": "offline-card",
                "version": 1,
                "sku": "GIAY-01",
                "name": "Synthetic item",
                "price": None,
                "currency": "VND",
                "status": "draft",
                "stock": 5,
                "variants": [{"size": "38", "color": "trắng", "stock": 5}],
                "image_url": None,
                "provenance": {
                    "frame_ts_ms": event["frame_ts_ms"],
                    "speech_event_ids": [],
                    "edited_by": None,
                },
            }
        ]

    r = runner.Runner(
        "http://test",
        {"frame": [no_read], "product_seen": [open_card]},
        http=w.client,
        attachment_auth={w.sid: {"epoch": w.epoch, "source_token": w.source, "system_token": w.system}},
    )
    frame = await w.frame(10)
    r.evidence.receive(frame)
    task = asyncio.create_task(r.process(frame, w.now))
    await asyncio.wait_for(began.wait(), 1)
    if not late_error:
        assert await task == "failed"
    seen = make_event(
        "product_seen",
        w.sid,
        10,
        "replay",
        category="sneaker",
        attributes={},
        confidence=1,
        is_new=True,
        frame_ts_ms=10,
        caused_by=[frame["event_id"]],
    )
    await w.ingest(seen)
    stored = next(e for e in await w.events() if e["event_id"] == seen["event_id"])
    r.cursor = stored["seq"]
    r.evidence.receive(stored)
    assert await r.process(stored, w.now) == "done"
    release.set()
    assert await task == "failed"
    pair = await w.comment(ts=15)
    after = await w.frame(20)
    r.evidence.receive(after)
    events = await w.events()
    card = next(e for e in events if e["type"] == "card")
    proof = {
        "after_frame_event_id": after["event_id"],
        "handled_frames": [copy.deepcopy(r.evidence.terminals[frame["event_id"]])],
    }

    def covered(value):
        return w.service.check_evidence(
            events,
            receipts,
            binding,
            next(e for e in events if e["event_id"] == pair[0]["event_id"]),
            events[-1]["seq"],
            value,
        )

    receipts = await w.store.source_receipts(w.sid)
    binding = await w.store.attachment_binding(w.sid)
    result = (covered(proof), proof, calls, events, seen, card, r)
    return result, covered


@pytest.mark.parametrize("late_error", [False, True])
async def test_admitted_stored_output_reaches_consistent_terminal(world, installed, late_error):  # noqa: F811
    result, covered = await chain(world, late_error)
    valid, proof, calls, events, seen, card, r = result
    try:
        assert len([e for e in events if e["type"] == "product_seen"]) == 1
        assert len([e for e in events if e["type"] == "card"]) == 1 and calls == [seen["event_id"]]
        assert not any(e["type"] == "model_call" for e in events)
        assert valid, proof
        assert proof["handled_frames"][0]["result"] == "product_seen"
    finally:
        await r.evidence.close()


async def test_server_accepts_only_matching_stored_product_chain(world, installed):  # noqa: F811
    result, covered = await chain(world, False)
    _, proof, calls, events, seen, card, r = result
    try:
        proof["handled_frames"] = [
            {
                "frame_event_id": seen["caused_by"][0],
                "result": "product_seen",
                "product_seen_event_id": seen["event_id"],
                "card_event_ids": [card["event_id"]],
            }
        ]
        assert covered(proof)
        proof["handled_frames"] = [{"frame_event_id": seen["caused_by"][0], "result": "read_error"}]
        assert not covered(proof)
    finally:
        await r.evidence.close()


def bound_worker(w, handlers=None):
    return runner.Runner(
        "http://test",
        handlers or {},
        http=w.client,
        attachment_auth={w.sid: {"epoch": w.epoch, "source_token": w.source, "system_token": w.system}},
    )


async def saved_recognition(w, r, ts=10):
    frame = await w.frame(ts)
    r.evidence.receive(frame)
    r.evidence.frame_finished(frame, [], read_error=True)
    event = make_event(
        "product_seen",
        w.sid,
        ts,
        "replay",
        category="sneaker",
        attributes={},
        confidence=1,
        is_new=True,
        frame_ts_ms=ts,
        caused_by=[frame["event_id"]],
    )
    await w.ingest(event)
    stored = next(e for e in await w.events() if e["event_id"] == event["event_id"])
    return frame, stored


@pytest.mark.parametrize("change", ["missing_seq", "wrong_seq", "body", "unadmitted", "session", "frame"])
async def test_invalid_record_never_dispatches_product_or_creates_positive_proof(world, installed, change):  # noqa: F811
    calls = []

    async def handler(event, ctx):
        calls.append(event["event_id"])
        return []

    r = bound_worker(world, {"product_seen": [handler]})
    frame, saved = await saved_recognition(world, r)
    event = copy.deepcopy(saved)
    before = copy.deepcopy(await world.events())
    if change == "missing_seq":
        event.pop("seq")
    elif change == "wrong_seq":
        event["seq"] += 1
    elif change == "body":
        event["category"] = "shirt"
    elif change == "unadmitted":
        event["event_id"] = "not-stored"
        event["seq"] += 1
    elif change == "session":
        event["session_id"] = "different-session"
        r.attachment_auth["different-session"] = dict(r.attachment_auth[world.sid])
    elif change == "frame":
        event["frame_ts_ms"] += 1
    try:
        assert await r.process(event, world.now) == "failed"
        assert not calls and before == await world.events()
        assert r.evidence.terminals[frame["event_id"]]["result"] == "read_error"
        assert not getattr(r, "_c5_stored_terminals", {})
    finally:
        await r.evidence.close()


@pytest.mark.parametrize("failure", [True, False])
async def test_failed_or_empty_product_handler_never_completes_a_positive_frame(world, installed, failure):  # noqa: F811
    async def handler(event, ctx):
        if failure:
            raise runner.Failed("stored product did not complete")
        return []

    r = bound_worker(world, {"product_seen": [handler]})
    frame, event = await saved_recognition(world, r)
    try:
        assert await r.process(event, world.now) == ("failed" if failure else "done")
        assert r.evidence.terminals[frame["event_id"]]["result"] == "read_error"
        assert not getattr(r, "_c5_stored_terminals", {})
        assert len([e for e in await world.events() if e["type"] == "product_seen"]) == 1
        assert not [e for e in await world.events() if e["type"] == "card"]
    finally:
        await r.evidence.close()


async def test_refused_source_repost_has_no_product_handler_side_effect(world, installed):  # noqa: F811
    calls = []

    async def handler(event, ctx):
        calls.append(event["event_id"])
        return []

    r = bound_worker(world, {"product_seen": [handler]})
    frame, event = await saved_recognition(world, r)
    r.attachment_auth[world.sid]["source_token"] = "invalid-source"
    before = await world.events()
    try:
        assert await r.process(event, world.now) == "failed"
        assert not calls and before == await world.events()
        assert event["event_id"] not in r.evidence.post_sequences
        assert r.evidence.terminals[frame["event_id"]]["result"] == "read_error"
    finally:
        await r.evidence.close()


async def test_later_page_recognition_preserves_stored_sequence_and_does_not_advance_cursor(world, installed):  # noqa: F811
    for ts in range(550):
        await world.frame(ts)
    r = bound_worker(world)
    frame, event = await saved_recognition(world, r, ts=600)
    receipt = copy.deepcopy((await world.store.source_receipts(world.sid))[event["event_id"]])
    before = await world.events()
    assert event["seq"] > 550
    try:
        assert await c5_offline.register_stored_product(r, event)
        assert r.evidence.post_sequences[event["event_id"]] == event["seq"]
        assert r.cursor == 0 and before == await world.events()
        assert (await world.store.source_receipts(world.sid))[event["event_id"]] == receipt
        assert r.evidence.terminals[frame["event_id"]]["result"] == "read_error"
    finally:
        await r.evidence.close()


async def test_duplicate_repost_does_not_promote_an_old_epoch_receipt(world, installed):  # noqa: F811
    from fastapi import HTTPException

    result, _ = await chain(world, False)
    _, proof, _, _, seen, _, r = result
    old_receipt = copy.deepcopy((await world.store.source_receipts(world.sid))[seen["event_id"]])
    world.epoch, world.source, world.system = "epoch-2", "next-source", "next-system"
    await world.configure()
    r.attachment_auth[world.sid] = {
        "epoch": world.epoch,
        "source_token": world.source,
        "system_token": world.system,
    }
    events = await world.events()
    event = next(e for e in events if e["event_id"] == seen["event_id"])
    try:
        assert await c5_offline.register_stored_product(r, event)
        receipts = await world.store.source_receipts(world.sid)
        assert receipts[event["event_id"]] == old_receipt and old_receipt["epoch"] == "epoch-1"
        binding = await world.store.attachment_binding(world.sid)
        with pytest.raises(HTTPException, match="earlier worker epoch"):
            world.service.check_evidence(
                events,
                receipts,
                binding,
                next(e for e in events if e["type"] == "comment"),
                events[-1]["seq"],
                proof,
            )
    finally:
        await r.evidence.close()


async def test_offline_read_failure_remains_in_lane_accounting(world, installed):  # noqa: F811
    async def failed(event, ctx):
        raise runner.Failed("offline read unavailable")

    r = bound_worker(world, {"frame": [failed]})
    frame = await world.frame(10)
    try:
        r.receive(frame)
        lane = r.vision[world.sid]
        while lane.tasks:
            await asyncio.gather(*list(lane.tasks))
        assert r.stats()["vision"]["failed"] == 1 and r.stats()["vision"]["done"] == 0
        assert r.evidence.terminals[frame["event_id"]]["result"] == "read_error"
    finally:
        await r.evidence.close()


def test_offline_process_patch_is_scoped_and_cleanup_restores_default(monkeypatch):
    original = runner.Runner.process
    with monkeypatch.context() as local:
        local.setattr(runner.Runner, "process", original)
        c5_offline.install_frame_replay(runner.Runner, lambda *args, **kwargs: None)
        assert runner.Runner.process is not original
    assert runner.Runner.process is original


@pytest.mark.parametrize("key_set", [False, True])
def test_default_worker_start_does_not_install_offline_process(monkeypatch, key_set):
    from measure import c5_proc, c5_progress

    original = runner.Runner.process
    started = []
    monkeypatch.setattr(c5_proc, "proof", lambda module: {"offline_loop": None, "model_key_set": key_set})
    monkeypatch.setattr(c5_proc, "say", lambda *args, **kwargs: None)
    monkeypatch.setattr(c5_proc, "PROGRESS", None)
    monkeypatch.setattr(c5_progress, "install", lambda say: None)
    monkeypatch.setattr(c5_proc.c5_bind, "credentials", lambda: None)
    monkeypatch.setattr(c5_proc.signal, "signal", lambda *args: None)
    monkeypatch.setattr(c5_proc.sys, "argv", ["c5_proc", "worker"])
    monkeypatch.setattr(c5_proc.runpy, "run_module", lambda *args, **kwargs: started.append(args))
    monkeypatch.setattr(c5_offline, "install", lambda *args: pytest.fail("offline patch on default path"))
    c5_proc.main()
    assert started == [("worker",)] and runner.Runner.process is original
