"""One bound session keeps real completion evidence across media loops; no network or model."""

import asyncio
import copy
import json
import signal
from types import SimpleNamespace

import pytest

from app.contracts import make_event
from measure import c5, c5_live, c5_offline, c5_proc
from measure.c5_dry import gold_item
from measure.c5_shop import Gold
from tests.test_attachments_runtime import world  # noqa: F401
from worker import runner
from worker.brain import labels
from worker.vision import frames, sam


@pytest.fixture
def offline(monkeypatch, tmp_path):
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


async def test_all_requested_streams_use_their_exact_existing_gold(offline):
    info = c5_offline.install(1, lambda *args, **kw: None, loops=5)
    client = labels.LABELER.client_for(None)
    rows = [row for k in range(1, 6) for row in c5.streams(k)[0]]
    assert len(rows) == len(client.rows) == 500 and info["gold_rows"] == 500
    for k in range(1, 6):
        source = c5.streams(k)[0]
        prompt = [{"content": json.dumps({"comments": [{"comment_id": r["comment_id"]} for r in source]})}]
        response = await client.responses(prompt, {})
        assert response.data["items"] == [gold_item(r) for r in source]


@pytest.mark.parametrize("failure", [None, "second_media", "snapshot"])
async def test_orchestrator_keeps_real_prefix_once_and_cleans_up(
    world,  # noqa: F811
    offline,
    monkeypatch,
    tmp_path,
    failure,
):
    """Only transport/process/clock plumbing is replaced; producer admission and coverage are real."""
    from app import settings
    from measure import c5_shop
    from worker.brain import card as cardbrain

    c5_offline.install(1, lambda *args, **kw: None)
    w = world
    workers, media, outcomes, cards, observations, saved = [], [], [], [], [], []
    finished = []
    watch_ref = []
    monkeypatch.setattr(cardbrain, "SESSIONS", {})
    monkeypatch.setattr(
        cardbrain,
        "CATALOG",
        [
            {
                "sku": "GIAY-01",
                "name": "Synthetic item",
                "category": "sneaker",
                "variants": [{"size": "38", "color": "trắng", "stock": 5}],
            }
        ],
    )

    async def no_read(event, ctx):
        raise runner.Failed("no offline model")

    async def open_card(event, ctx):
        observations.append(event["event_id"])
        return await cardbrain.on_product_seen(event, ctx)

    async def deliver(r, event, process=True):
        stored = next(e for e in await w.events() if e["event_id"] == event["event_id"])
        r.cursor = stored["seq"]
        r.evidence.receive(stored)
        if process:
            await r.process(stored, w.now)
        return stored

    async def media_loop(child):
        k = len(media)
        r = workers[-1].runner
        if k == 2:
            assert cardbrain.SESSIONS[w.sid].cards[cards[0]["card_id"]]["status"] == "retired"
        w.now += 1000
        frame = await w.frame(k * 100)
        await deliver(r, frame)
        watch_ref[0].frames.append(frame)
        seen = make_event(
            "product_seen",
            w.sid,
            k * 100,
            "replay",
            category="sneaker",
            attributes={},
            confidence=1,
            is_new=True,
            frame_ts_ms=k * 100,
            caused_by=[frame["event_id"]],
        )
        await w.ingest(seen)
        await deliver(r, seen)
        card = next(
            e for e in await w.events() if e["type"] == "card" and seen["event_id"] in e.get("caused_by", [])
        )
        cards.append(card)
        await w.price(card, 150000)
        pair = await w.comment(ts=k * 100 + 1, cid=f"buyer-{k}")
        for event in pair:
            await deliver(r, event, False)
        after = await w.frame(k * 100 + 2)
        await deliver(r, after)
        # The actual evidence evaluator posts only the terminals it genuinely handled.
        await r.evidence.task
        events = await w.events()
        terminal_ids = set(r.evidence.terminals)
        required_ids = {
            e["event_id"] for e in events if e["type"] == "frame" and e["ts_ms"] <= pair[0]["ts_ms"]
        }
        attachment = next(
            (e for e in events if e["type"] == "comment_attachment" and e["comment_id"] == f"buyer-{k}"), None
        )
        outcomes.append(
            {
                "required": required_ids,
                "actual": terminal_ids,
                "ready": attachment and attachment["outcome"] == "ready",
            }
        )
        saved.append(copy.deepcopy(events))
        # Even a second evaluation cannot dispatch earlier product/order effects again.
        r.evidence.wake()
        await r.evidence.task
        assert events == await w.events()

    class FakeChild:
        def __init__(self, module, args, env, log_path):
            self.module, self.env, self.log_path = module, env, log_path
            self.proc = None
            self.reader = None
            self.lines, self.wait_lines, self.cutouts, self.said = [], [], [], {}
            self.failures, self.progress, self.summary = 0, {}, "synthetic media"
            self.ready = asyncio.Event()
            self.term_calls = 0

        async def start(self):
            self.proc = SimpleNamespace(returncode=None)
            self.proc.kill = lambda: setattr(self.proc, "returncode", -9)
            self.reader = asyncio.create_task(asyncio.sleep(0))
            if self.module == "worker":
                self.runner = runner.Runner(
                    "http://test",
                    {"frame": [no_read], "product_seen": [open_card], "card": [cardbrain.on_card]},
                    http=w.client,
                    attachment_auth={
                        w.sid: {"epoch": w.epoch, "source_token": w.source, "system_token": w.system}
                    },
                )
                workers.append(self)
                self.ready.set()
            else:
                media.append(self)
                if failure == "second_media" and len(media) == 2:
                    raise RuntimeError("second media setup failed")
                await media_loop(self)

        async def said_kind(self, kind, timeout_s):
            return {
                "offline_loop": int(self.env["C5_OFFLINE"]),
                "offline_loops": int(self.env.get("C5_OFFLINE_LOOPS", "1")),
            }

        async def snapshot(self):
            if failure == "snapshot":
                raise RuntimeError("snapshot failed")
            self.said["waits"] = {"waits": {w.sid: {"many": len(media)}}}
            return self.said["waits"]

        async def term(self):
            self.term_calls += 1
            assert self.term_calls == 1
            self.proc.returncode = 0
            await self.runner.evidence.close()
            return 0

        async def stop(self, grace_s=10):
            self.proc.returncode = 0
            return 0, False

    class FakeWatch:
        def __init__(self, http, sid, feed, shop):
            self.feed, self.frames = feed, []
            self.cursor = 0
            self.cap, self.capped = asyncio.Event(), None
            watch_ref.append(self)

        async def run(self):
            return

        async def frame_after(self, n, timeout_s):
            return self.frames[n]

        async def barrier(self, worker, tasks):
            for event in await w.events():
                self.feed.add(event["seq"], event)
                if event["seq"] > self.cursor and event["type"] == "card":
                    await worker.runner.process(event, w.now)
                self.cursor = max(self.cursor, event["seq"])
            return {"status": "SETTLED", "behind": 0}

        async def halt(self, task):
            await task

    class Passive:
        def __init__(self, *args, **kwargs):
            self.tasks, self.log = set(), []

        def start(self, *args):
            pass

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, path, **kw):
            if path == "/api/cost":
                return SimpleNamespace(json=lambda: {"session_cap_usd": 8.0, "session_warn_usd": 6.0})
            return await w.client.get(path, **kw)

        def __getattr__(self, name):
            return getattr(w.client, name)

    async def no_posts(*args, **kw):
        return 0

    monkeypatch.setattr(settings, "load_dotenv", lambda *args: None)
    monkeypatch.setattr(
        c5_live.c5_bind,
        "credentials",
        lambda: {
            "session": w.sid,
            "epoch": w.epoch,
            "source_token": w.source,
            "system_token": w.system,
            "principal": "synthetic-source",
        },
    )
    monkeypatch.setattr(c5_live, "Child", FakeChild)
    monkeypatch.setattr(c5_live, "Watch", FakeWatch)
    monkeypatch.setattr(c5_live, "check_proof", lambda *args: None)
    monkeypatch.setattr(c5_live, "t0_of", lambda frame: frame["ts_ms"])
    monkeypatch.setattr(c5_live, "replay_seen", lambda *args, **kw: [])
    monkeypatch.setattr(c5_live, "post_comments", no_posts)
    monkeypatch.setattr(c5_live, "post_seen", no_posts)
    monkeypatch.setattr(c5_shop, "Shop", Passive)
    monkeypatch.setattr(c5_shop, "Customers", Passive)
    monkeypatch.setattr(Gold, "load", lambda *args: Gold({"loop_ms": 1000}, {}))
    monkeypatch.setattr(c5, "finish", lambda events, run_file, *args: finished.append(run_file))
    monkeypatch.setattr("httpx.AsyncClient", Client)
    try:
        if failure:
            with pytest.raises(RuntimeError, match="second media|snapshot failed"):
                await c5_live.main("http://test", w.sid, 2, c5.C5 / "catalog.json", tmp_path, False)
        else:
            await c5_live.main("http://test", w.sid, 2, c5.C5 / "catalog.json", tmp_path, False)
        if not failure:
            assert all(row["ready"] and row["required"] <= row["actual"] for row in outcomes), outcomes
            assert len(outcomes[1]["required"]) == 3
            events = await w.events()
            r = workers[0].runner
            pair = next(e for e in events if e["type"] == "comment" and e["comment_id"] == "buyer-2")
            proof = {
                "after_frame_event_id": w.frames[-1]["event_id"],
                "handled_frames": [
                    value for key, value in r.evidence.terminals.items() if key in outcomes[1]["required"]
                ],
            }
            args = (
                events,
                await w.store.source_receipts(w.sid),
                await w.store.attachment_binding(w.sid),
                pair,
                events[-1]["seq"],
            )
            assert w.service.check_evidence(*args, proof)
            proof["handled_frames"] = proof["handled_frames"][1:]
            assert not w.service.check_evidence(*args, proof)  # retaining only the new loop cannot pass
            assert len(observations) == len(set(observations)) == 2
            assert len({e["order_id"] for e in events if e["type"] == "order"}) == 2
            assert len([e for e in events if e["type"] == "card" and e["version"] == 1]) == 2
            latest = {e["card_id"]: e for e in events if e["type"] == "card"}
            assert len(latest) == 2 and all(e["status"] == "retired" for e in latest.values())
            assert all(e["status"] == "retired" for e in cardbrain.SESSIONS[w.sid].cards.values())
            assert [row["waits"] for row in finished[0]["loops"]] == [{"many": 1}, {"many": 1}]
            assert not any(e["type"] == "model_call" for e in events)
            assert workers[0].env["C5_OFFLINE"] == "1" and workers[0].env["C5_OFFLINE_LOOPS"] == "2"
        assert len(workers) == 1 and workers[0].term_calls == 1
        assert all(c.proc.returncode == 0 for c in workers + media)
    finally:
        for worker in workers:
            await worker.runner.evidence.close()


def test_nonterminating_snapshot_preserves_worker(monkeypatch):
    from worker.brain import card

    said, exited = [], []
    monkeypatch.setattr(c5_proc, "PROGRESS", None)
    monkeypatch.setattr(card, "SESSIONS", {"session": SimpleNamespace(waits={"many": 2})})
    monkeypatch.setattr(c5_proc, "say", lambda kind, **kw: said.append((kind, kw)))
    monkeypatch.setattr(c5_proc.os, "_exit", exited.append)
    c5_proc.on_snapshot(None, None)
    assert said == [("waits", {"waits": {"session": {"many": 2}}})] and not exited


async def test_child_snapshot_requires_a_new_report_and_keeps_the_process(tmp_path):
    child = c5_live.Child("worker", [], {}, tmp_path / "worker.log")
    output = asyncio.StreamReader()
    old = {"kind": "waits", "waits": {"s": {"many": 1}}}
    fresh = {"kind": "waits", "waits": {"s": {"many": 2}}}
    child.snapshots.append(old)
    sent = []

    def send(sig):
        sent.append(sig)
        output.feed_data((c5_proc.TAG + json.dumps(fresh) + "\n").encode())

    child.proc = SimpleNamespace(returncode=None, stdout=output, send_signal=send)
    child.reader = asyncio.create_task(child.read())
    try:
        assert await child.snapshot() == fresh
        assert sent == [signal.SIGUSR1] and child.proc.returncode is None
        assert child.snapshots == [old, fresh]
    finally:
        output.feed_eof()
        await child.reader
