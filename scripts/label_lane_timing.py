"""Offline timing of comment labeling under a fake model with a fixed latency: no network, no model call.

Feeds synthetic comments of one session through the in-memory API and the worker's label handlers, with
the model replaced by a transport that sleeps `--latency-ms` and labels every comment `other`. For each
scenario it reports, per comment, the time from the comment's receipt (its `ingested_ms`) to its label
being stored, the number of label_comments calls, the batch sizes, and the input tokens those calls would
have sent (the prompt's characters / 4, the usual estimate for this kind of text).

Scenarios (current batching config: 20 per batch, 1 s window):
  sparse   one comment every 3 s, twelve comments
  burst    ten comments within 1 s, three rounds, round starts 1.5 s apart
  trickle  one comment every 1.5 s, twelve comments (a comment arrives during every open call)

Run it on the commit before and after a batching change and compare the tables:
  uv run python scripts/label_lane_timing.py --latency-ms 2000
"""

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.contracts import make_event, now_ms  # noqa: E402
from app.main import create_app  # noqa: E402
from app.store import MemoryStore  # noqa: E402
from meta.client import MetaClient  # noqa: E402
from meta.meter import Meter, memory_spend  # noqa: E402
from meta.transport import Reply, responses_reply  # noqa: E402
from worker.brain.labels import Labeler  # noqa: E402
from worker.runner import Runner, build  # noqa: E402

SESSION = "lane-timing"
WINDOW_MS = 4000  # the attachment deadline after a comment's receipt (app/attachments.py)

SCENARIOS = {
    "sparse": [(i * 3.0, 1) for i in range(12)],
    "burst": [(r * 1.5 + i * 0.1, 1) for r in range(3) for i in range(10)],
    "trickle": [(i * 1.5, 1) for i in range(12)],
}


class FixedLatencyModel:
    """Answers label_comments after a fixed delay, labeling every comment `other`; records each call."""

    def __init__(self, latency_s: float):
        self.latency_s = latency_s
        self.calls: list[dict] = []

    async def post(self, path: str, body: dict) -> Reply:
        comments = json.loads(body["input"][1]["content"])["comments"]
        chars = sum(len(m["content"]) for m in body["input"])
        self.calls.append({"size": len(comments), "chars": chars})
        await asyncio.sleep(self.latency_s)
        items = [
            {
                "comment_id": c["comment_id"],
                "label": "other",
                "reference_kind": "none",
                "sku": None,
                "size": None,
                "color": None,
                "qty": None,
                "contact": "none",
            }
            for c in comments
        ]
        return responses_reply(json.dumps({"items": items}), chars // 4, 20 * len(items))

    async def aclose(self) -> None:
        pass


def card(ts_ms: int) -> dict:
    return make_event(
        "card",
        SESSION,
        ts_ms,
        "sim",
        event_id=f"{SESSION}-card-v1",
        card_id="card-a",
        sku="GIAY-01",
        name="Sneaker",
        price=250000,
        currency="VND",
        variants=[{"size": "40", "color": "white", "stock": 5}],
        stock=5,
        image_url=f"sim://{SESSION}/card-a.png",
        version=1,
        status="live",
        provenance={"frame_ts_ms": ts_ms, "speech_event_ids": [], "edited_by": None},
    )


def comment(n: int, ts_ms: int) -> dict:
    return make_event(
        "comment",
        SESSION,
        ts_ms,
        "sim",
        event_id=f"{SESSION}-c{n:03d}",
        comment_id=f"{SESSION}-c{n:03d}",
        user_ref=f"u-{n % 7}",
        text="is this one still available, which sizes are left",
        reply_to=None,
    )


async def post(client: httpx.AsyncClient, runner: Runner, event: dict) -> None:
    r = await client.post(f"/api/sessions/{SESSION}/events", json=[event])
    r.raise_for_status()
    await runner.handle(event)


async def run_scenario(name: str, latency_s: float) -> dict:
    store = MemoryStore()
    await store.open()
    app = create_app(store)
    app.state.store = store
    model = FixedLatencyModel(latency_s)
    holder: dict = {}

    def client_for(ctx):
        async def emit(fields):
            await holder["runner"].post([build(ctx.event, {"type": "model_call", **fields}, now_ms())])

        return MetaClient(model, Meter(memory_spend([0.0]), 30, 300, 20), emit)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        labeler = Labeler(client_for=client_for)
        holder["runner"] = runner = Runner("http://test", labeler.handlers(), http=client)
        await post(client, runner, card(now_ms()))
        loop = asyncio.get_running_loop()
        start = loop.time()
        n = 0
        for at, count in SCENARIOS[name]:
            await asyncio.sleep(max(start + at - loop.time(), 0))
            for _ in range(count):
                n += 1
                await post(client, runner, comment(n, now_ms()))
        while labeler.tasks:  # a finished task leaves the set on the next loop round
            await asyncio.sleep(0)
            await asyncio.gather(*list(labeler.tasks), return_exceptions=True)
        events = await store.read(SESSION, 0, 10_000)
    received = {e["comment_id"]: e["ingested_ms"] for e in events if e["type"] == "comment"}
    waits = sorted(
        e["ingested_ms"] - received[e["comment_id"]] for e in events if e["type"] == "comment_labeled"
    )
    assert len(waits) == n, (len(waits), n)
    return {
        "scenario": name,
        "latency_ms": int(latency_s * 1000),
        "comments": n,
        "calls": len(model.calls),
        "sizes": [c["size"] for c in model.calls],
        "median_ms": int(statistics.median(waits)),
        "max_ms": waits[-1],
        "over_window": sum(w > WINDOW_MS for w in waits),
        "est_input_tokens": sum(c["chars"] for c in model.calls) // 4,
    }


def table(rows: list[dict]) -> str:
    head = "| scenario | latency ms | comments | calls | batch sizes | median ms | max ms | past 4 s | est input tokens |"
    lines = [head, "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        sizes = " ".join(str(s) for s in r["sizes"])
        lines.append(
            f"| {r['scenario']} | {r['latency_ms']} | {r['comments']} | {r['calls']} | {sizes} | {r['median_ms']} "
            f"| {r['max_ms']} | {r['over_window']} | {r['est_input_tokens']} |"
        )
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--latency-ms", type=int, default=2000, help="the fake model's latency per call")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), action="append", help="default: all")
    args = parser.parse_args()
    rows = [await run_scenario(name, args.latency_ms / 1000) for name in args.scenario or sorted(SCENARIOS)]
    print(table(rows))


if __name__ == "__main__":
    asyncio.run(main())
