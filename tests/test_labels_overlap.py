"""Label batches of one session overlap: a comment that arrives while the session's label call is open gets
its own batch after its own window, instead of waiting for that call to end.

The fake model has a latency per call; the oracle answers from the hand labels (no real model call).
"""

import asyncio

from app.contracts import now_ms
from connectors.sim.comments import SESSION, events
from connectors.sim.replay import plan
from measure.core import latest_labels
from tests.test_labels import GOLD, OracleTransport, drain, feed, sizes, wire
from worker.brain import labels as labels_mod
from worker.brain.shield import scan


def clock() -> float:
    return asyncio.get_running_loop().time()


class LatencyOracle(OracleTransport):
    """The oracle with a latency per call, taken in call order (the last one repeats); when each call started
    and ended (both in call order), and how many were open at once. `requests` (so `sizes`) is in landing
    order, as the oracle records a request when it answers it."""

    def __init__(self, *latencies: float):
        super().__init__()
        self.latencies = list(latencies)
        self.started, self.ended, self.open, self.most = [], [], 0, 0

    async def post(self, path, body):
        wait = self.latencies.pop(0) if len(self.latencies) > 1 else self.latencies[0]
        i = len(self.started)
        self.started.append(clock())
        self.ended.append(None)
        self.open += 1
        self.most = max(self.most, self.open)
        try:
            await asyncio.sleep(wait)
            return await super().post(path, body)
        finally:
            self.open -= 1
            self.ended[i] = clock()


def stream(n: int, *, contact: bool = False):
    """The first card of the simulated stream and its first `n` comments with (or without) a shield signal."""
    timed = plan(events(), SESSION, now_ms(), 1.0)
    card = [p for p in timed if p[1]["type"] == "card"][:1]
    comments = [p for p in timed if p[1]["type"] == "comment" and bool(scan(p[1]["text"])) == contact]
    return card, comments[:n]


def ids(schedule) -> list[str]:
    return [e["comment_id"] for _, e in schedule]


async def test_a_comment_arriving_during_a_call_does_not_wait_for_it(mem_app, monkeypatch):
    """The second comment of the session arrives while the first one's call is open (0.35 s of it left): its
    batch is cut after its own window (0.05 s), while that call is still open."""
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.05)
    _, store, client = mem_app
    transport = LatencyOracle(0.5)
    labeler, runner = wire(client, transport)
    card, (first, second) = stream(2)
    await feed(client, runner, card + [first])
    await asyncio.sleep(0.15)
    assert transport.open == 1
    arrived = clock()
    await feed(client, runner, [second])
    await drain(labeler)
    assert sizes(transport) == [1, 1]
    assert transport.started[1] - arrived < 0.25
    assert transport.started[1] < transport.ended[0]
    assert transport.most == 2
    latest = latest_labels(await store.read(SESSION, 0, 100))
    assert {cid: e["label"] for cid, e in latest.items()} == {
        cid: GOLD[cid]["label"] for cid in ids([first, second])
    }


async def test_latest_label_of_a_comment_survives_an_older_batch_landing_later(mem_app, monkeypatch):
    """Batch 1 (slow) is still open when batch 2 (fast) is cut and lands. When batch 1 lands after it, every
    comment's latest label is the one its own batch gave: the model labels a comment once, in its own batch,
    and a contact comment's receipt hide stays the earlier of its two labels."""
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.05)
    _, store, client = mem_app
    transport = LatencyOracle(0.5, 0.05)
    labeler, runner = wire(client, transport)
    card, plain = stream(3)
    _, contact = stream(1, contact=True)
    await feed(client, runner, card + plain[:1])
    await asyncio.sleep(0.15)
    assert transport.open == 1
    await feed(client, runner, plain[1:] + contact)
    await drain(labeler)
    assert sizes(transport) == [3, 1]  # landing order: the fast batch of three lands before the slow one
    assert transport.started[1] < transport.ended[0] and transport.ended[1] < transport.ended[0]
    out = await store.read(SESSION, 0, 100)
    labels = [e for e in out if e["type"] == "comment_labeled"]
    by_model = [e["comment_id"] for e in labels if "infer_start_ms" in e["timing"]]
    assert sorted(by_model) == sorted(ids(plain + contact))
    assert by_model[-1] == ids(plain)[0]  # the slow batch's label is stored last
    latest = latest_labels(out)
    assert {cid: latest[cid]["label"] for cid in ids(plain + contact)} == {
        cid: GOLD[cid]["label"] for cid in ids(plain + contact)
    }
    cid = ids(contact)[0]
    first = next(e for e in labels if e["comment_id"] == cid)
    assert "infer_start_ms" not in first["timing"] and first["action"] == "hide"
    assert latest[cid]["action"] == "hide" and "infer_start_ms" in latest[cid]["timing"]


async def test_a_session_opens_at_most_the_lane_cap_of_calls(mem_app, monkeypatch):
    """Sixty comments at once with two lanes: two batches of twenty open together, the third waits for a lane
    and is cut when the first call ends."""
    monkeypatch.setattr(labels_mod, "BATCH_LANES", 2)
    _, store, client = mem_app
    transport = LatencyOracle(0.3)
    labeler, runner = wire(client, transport)
    card, comments = stream(60)
    await feed(client, runner, card + comments)
    await asyncio.sleep(0.05)
    assert labeler.backlog() == {"waiting": 20, "in_flight": 40}
    await drain(labeler)
    assert sizes(transport) == [20, 20, 20]
    assert transport.most == 2
    assert transport.started[2] >= transport.ended[0]
    assert labeler.backlog() == {"waiting": 0, "in_flight": 0}
    labeled = {e["comment_id"] for e in await store.read(SESSION, 0, 500) if e["type"] == "comment_labeled"}
    assert labeled == set(ids(comments))
