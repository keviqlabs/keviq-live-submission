"""measure.model_compare cap: one call's hold, retries, the worst case of a POST, the phase cap, cancelled POSTs
and a stopped run. No network: the Model API is a fake transport."""

import asyncio
import json

import pytest

from measure import model_compare as m
from meta.transport import FakeTransport, Reply, responses_reply
from tests.test_model_compare import FRAME, b, runner

W_MIN, W_HIGH, W_WONDER = 0.006125, 0.035875, 0.166  # by hand below, before any reply raises the floors


class Gated:
    """Each POST waits on its own gate; `gates[i].set()` lets the i-th POST reply."""

    def __init__(self, text: str):
        self.text, self.requests, self.gates = text, [], []

    async def post(self, path, body):
        self.requests.append((path, body))
        self.gates.append(asyncio.Event())
        await self.gates[-1].wait()
        return responses_reply(self.text, 1000, 50)


async def settle():
    for _ in range(200):
        await asyncio.sleep(0)


async def test_a_call_that_ends_gives_back_its_hold_while_another_is_in_flight(tmp_path):
    transport = Gated(json.dumps(b()))
    r = m.Runner(transport, tmp_path, lambda f: b"x", 4.1 * W_MIN)  # two calls of 2w fit, a third does not
    plan = [({"t": i, "frame": f"f_{i:05d}.jpg"}, "spark-minimal", "B") for i in (1, 2, 3)]
    task = asyncio.create_task(r.run(plan, concurrency=2, log=lambda s: None))
    try:
        await settle()
        assert len(transport.requests) == 2 and r.budget.held == pytest.approx(4 * W_MIN)
        transport.gates[0].set()  # call 1 replies and ends; call 2 is still in flight
        await settle()
        assert r.budget.held == pytest.approx(2 * W_MIN)  # call 2's hold only
        assert len(transport.requests) == 2 and r.stopped.startswith("cap:")  # call 3 was refused
    finally:
        for gate in transport.gates:
            gate.set()
        summary = await task
    assert summary["called"] == 2 and r.budget.held == pytest.approx(0, abs=1e-12)


async def test_a_retry_past_the_call_hold_is_checked_again(tmp_path):
    busy = Reply(429, {"error": "busy"}, {"retry-after": "0"})
    r, t = runner(tmp_path, [busy, busy, responses_reply(json.dumps(b()))])

    async def others_spend_the_rest(delay):  # while this call waits to retry
        r.budget.spent = r.budget.cap

    r.client.sleep = others_spend_the_rest
    out = await r.one(FRAME, "spark-minimal", "B")  # POSTs 1 and 2 use the call's 2w; POST 3 needs a new hold
    assert len(t.requests) == 2 and out["posts"] == 2 and out["error"] == "experiment_cap"
    assert r.stopped.startswith("cap at a POST") and r.budget.held == pytest.approx(0, abs=1e-12)


async def test_the_worst_case_of_a_post_follows_the_floors_prices_and_margins(tmp_path):
    r, _ = runner(tmp_path, [responses_reply(json.dumps(b()), 2000, 600)])
    assert r.w_post("spark-minimal") == pytest.approx(W_MIN) == (1500 * 1.25 + 1000 * 4.25) / 1e6
    assert r.w_post("spark-high") == pytest.approx(W_HIGH) == (1500 * 1.25 + 8000 * 4.25) / 1e6
    assert r.w_post("wonder-high") == pytest.approx(W_WONDER) == (1500 * 4.00 + 8000 * 20.00) / 1e6
    await r.one(FRAME, "spark-minimal", "B")  # 2000 in and 600 out: 1.2 x in and 2 x out pass both floors
    grown = (1.2 * 2000 * 1.25 + 2 * 600 * 4.25) / 1e6
    assert r.w_post("spark-minimal") == pytest.approx(grown) and r.w_post("spark-high") == pytest.approx(
        W_HIGH
    )
    assert runner(tmp_path, [])[0].w_post("spark-minimal") == pytest.approx(grown)  # after a restart too


async def test_the_spark_phase_runs_under_its_own_cap(tmp_path, monkeypatch):
    class NoNetwork(FakeTransport):
        def __init__(self, timeout):
            super().__init__([])

    runners = []

    class Recorded(m.Runner):
        def __init__(self, *args):
            super().__init__(*args)
            runners.append(self)

    (tmp_path / m.MANIFEST).write_text(json.dumps({"frames": []}))
    monkeypatch.setattr(m, "OUT", tmp_path)
    monkeypatch.setattr(m, "committed", lambda path: True)
    monkeypatch.setattr(m, "check_manifest", lambda man, gold: None)
    monkeypatch.setattr(m, "Runner", Recorded)
    monkeypatch.setattr("meta.transport.HttpTransport", NoNetwork)
    assert m.PHASES["spark"]["cap"] == 2.50 < m.TOTAL_CAP
    assert (await m.run_phase("spark", tmp_path, 1))["planned"] == 0
    assert [x.budget.cap for x in runners] == [2.50]


class Cancelled:
    requests: list = []

    async def post(self, path, body):
        raise asyncio.CancelledError  # a time limit cut the POST: no reply, its cost is unknown


async def test_a_cancelled_post_is_a_no_reply_live_and_after_a_restart(tmp_path):
    r = m.Runner(Cancelled(), tmp_path, lambda f: b"x", 5.0)
    with pytest.raises(asyncio.CancelledError):
        await r.one(FRAME, "spark-minimal", "A")
    assert [e["error"] for e in m.read_jsonl(tmp_path / m.LEDGER)] == ["cancelled"]
    assert r.budget.unknown == pytest.approx(W_MIN) and r.budget.held == pytest.approx(0, abs=1e-12)
    again = m.Runner(
        Cancelled(), tmp_path, lambda f: b"x", 5.0
    )  # a restart cannot tell the effort: larger one
    assert again.budget.unknown == pytest.approx(W_HIGH)


async def test_queued_calls_do_not_run_once_the_run_has_stopped(tmp_path):
    r, t = runner(tmp_path, [Reply(400, {"error": "bad request"})] * m.STOP_AFTER_ERRORS)
    plan = [({"t": i, "frame": f"f_{i:05d}.jpg"}, "spark-minimal", "B") for i in range(1, 8)]
    summary = await r.run(plan, concurrency=1, log=lambda s: None)
    assert summary["called"] == m.STOP_AFTER_ERRORS == len(t.requests)
    assert summary["stopped"].startswith(f"{m.STOP_AFTER_ERRORS} failed calls in a row")


@pytest.mark.parametrize("failure", ["write", "validation"])
@pytest.mark.parametrize("reply", ["billed", "unknown"])
async def test_ledger_failure_stops_calls_and_keeps_attempted_cost(tmp_path, monkeypatch, failure, reply):
    previous = 5.0 - 2 * W_MIN
    (tmp_path / m.LEDGER).write_text(
        json.dumps({"event_id": "past", "model": m.SPARK, "cost_usd": previous, "error": None}) + "\n"
    )

    class NoReply(FakeTransport):
        async def post(self, path, body):
            self.requests.append((path, body))
            raise ConnectionError("synthetic no reply")

    t = (
        FakeTransport([responses_reply(json.dumps(b()), 1000, 1000)] * 7)
        if reply == "billed"
        else NoReply([])
    )
    r = m.Runner(t, tmp_path, lambda f: b"x", 5.0)
    if failure == "write":
        r.client.emit.path = tmp_path  # IsADirectoryError on the ledger destination only.
    else:

        def reject_event(*args, **kwargs):
            raise ValueError("synthetic validation failure")

        monkeypatch.setattr(m, "make_event", reject_event)
    plan = [({"t": i, "frame": f"f_{i:05d}.jpg"}, "spark-minimal", "B") for i in range(1, 8)]
    summary = await r.run(plan, concurrency=1, log=lambda s: None)
    assert len(t.requests) == 1, "an accounting failure must prevent the next POST"
    assert summary["stopped"].startswith("accounting_failure")
    assert m.read_jsonl(tmp_path / m.ROWS)[0]["error"] == "accounting_failure"
    assert r.budget.spent == pytest.approx(previous + (0.0055 if reply == "billed" else 0))
    assert r.budget.unknown == pytest.approx(W_MIN if reply == "unknown" else 0)
    assert r.budget.held == pytest.approx(0)
    assert await r.one({"t": 9}, "spark-minimal", "B") is None
    assert len(t.requests) == 1
    with pytest.raises(m.AccountingError, match="reconcile"):
        m.Runner(FakeTransport([]), tmp_path, lambda f: b"x", 5.0)


async def test_ledger_failure_prevents_an_inflight_calls_retry(tmp_path):
    transport = Gated("not json")
    r = m.Runner(transport, tmp_path, lambda f: b"x", 5.0)
    r.client.emit.path = tmp_path
    task = asyncio.create_task(
        r.run([({"t": i}, "spark-minimal", "B") for i in range(4)], concurrency=2, log=lambda s: None)
    )
    try:
        await settle()
        assert len(transport.requests) == 2
        transport.gates[0].set()
        await settle()
        assert len(transport.requests) == 2, "a failed ledger must stop queued calls immediately"
        transport.gates[1].set()
        summary = await task
        assert len(transport.requests) == 2
        assert summary["stopped"].startswith("accounting_failure")
        cost = (1000 * 1.25 + 50 * 4.25) / 1e6
        assert r.budget.spent == pytest.approx(2 * cost)
        assert r.budget.held == pytest.approx(0)
    finally:
        # A failing old implementation may schedule remaining calls; release all fake gates before exit.
        for _ in range(4):
            for gate in transport.gates:
                gate.set()
            await settle()
        await task


async def test_checkpoint_failure_prevents_even_the_first_post(tmp_path, monkeypatch):
    r, transport = runner(tmp_path, [responses_reply(json.dumps(b()))])
    from pathlib import Path

    original = Path.open

    def no_checkpoint(path, mode="r", *args, **kwargs):
        if path.name == "model-compare-accounting-pending.json":
            raise OSError("synthetic checkpoint failure")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_checkpoint)
    summary = await r.run([(FRAME, "spark-minimal", "B")], log=lambda s: None)
    assert transport.requests == []
    assert summary["stopped"].startswith("accounting_failure")
    assert r.budget.spent == r.budget.unknown == r.budget.held == 0


def test_command_reports_accounting_failure_with_nonzero_exit(tmp_path, monkeypatch):
    async def failed(*args):
        return {"stopped": "accounting_failure: synthetic"}

    monkeypatch.setattr(m, "OUT", tmp_path)
    monkeypatch.setattr(m, "run_phase", failed)
    assert m.main(["run", "--phase", "spark", "--gold", str(tmp_path)]) == 1
