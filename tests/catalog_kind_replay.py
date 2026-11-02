"""No-network component check using frozen comparison outputs, not the changed prompt.

Run as a module with --frozen pointing at the comparison evidence directory. Mode A
uses the saved coarse observations. Mode kind adds saved B0 kind/color/state only
where A said other. No latency, price, order, or attachment-readiness claim follows.
"""

import argparse
import asyncio
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx

from app.attachments import Attachments
from app.contracts import make_event
from app.current import current_at
from app.main import create_app
from app.settings import ROOT
from app.store import MemoryStore
from worker.brain import card
from worker.runner import Runner
from worker.vision import frames, spark


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


class NoModel(Runner):
    def client_for(self, event):
        raise AssertionError("model calls forbidden")


async def replay(frozen: Path, mode: str) -> dict:
    calls = rows(frozen / "model-compare-calls.jsonl")
    coarse = {r["t"]: r for r in calls if r["config"] == "spark-minimal" and r["schema"] == "A"}
    detailed = {r["t"]: r for r in calls if r["config"] == "spark-minimal" and r["schema"] == "B0"}
    scored = json.loads((frozen / "model-compare-score.json").read_text())
    truth = {r["t"]: r for r in scored["rows"] if r["config"] == "spark-minimal" and r["schema"] == "A"}
    stream_path = ROOT / "data/comments/c5/stream-1.jsonl"
    catalog_path = ROOT / "data/comments/c5/catalog.json"
    events_path = ROOT / "docs/evidence/2026-11-01/identity-1101-paid-0742-events.jsonl"
    comments = [r for r in rows(stream_path) if (r.get("order") or {}).get("product")]
    labels = {e["comment_id"]: e for e in rows(events_path) if e["type"] == "comment_labeled"}
    assert len(coarse) == len(detailed) == 70 and len(comments) == 28
    assert set(coarse) == set(detailed) == set(truth)
    card.CATALOG = json.loads(catalog_path.read_text())
    card.SESSIONS = {}
    spark.LAST = {}
    frames.load = lambda event: b"fake"
    frames.put = lambda *args: None
    store = MemoryStore()
    await store.open()
    app = create_app(store)
    app.state.store = store
    frame_rows, projections = [], []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        runner = NoModel("http://test", card.HANDLERS, http=http)
        ctx = SimpleNamespace(client=lambda: object())

        async def consume(t):
            data = copy.deepcopy(coarse[t]["data"])
            if mode == "kind" and data["category"] == "other":
                saved = detailed[t]["data"]
                data.update(
                    kind=saved.get("kind"),
                    identity_state="one_product" if saved.get("state") == "one_product" else "unclear",
                    color=saved.get("color"),
                    confidence=saved.get("confidence", 0),
                )

            async def reply(*args):
                return SimpleNamespace(data=data, infer_start_ms=1, infer_end_ms=2)

            spark.read_frame = reply
            output = await spark.see_product(
                {"event_id": f"frame-{t}", "session_id": "component", "ts_ms": t * 1000}, ctx
            )
            for part in output:
                part = {k: v for k, v in part.items() if k not in ["type", "_infer"]}
                event = make_event(
                    "product_seen",
                    "component",
                    t * 1000,
                    "sim",
                    event_id=f"ps-{t}",
                    caused_by=[f"frame-{t}"],
                    **part,
                )
                assert (await http.post("/api/sessions/component/events", json=[event])).status_code == 200
                await runner.handle(event)
                assert runner.failures == 0
            if not data["has_product"] or data["category"] == "none" or data["confidence"] < 0.5:
                item = None
            elif data["category"] == "other" and hasattr(card, "other_identity"):
                item, _ = card.other_identity(data, data["confidence"])
            else:
                item = card.match(
                    data["category"], data["color"].strip().lower() if data.get("color") else None
                )
            sku, gold = item["sku"] if item else None, truth[t]
            correct = sku in gold["allowed"] if gold["stratum"] == "boundary" else sku == gold["gold"]
            frame_rows.append(
                {
                    "t": t,
                    "stratum": gold["stratum"],
                    "gold": gold["gold"],
                    "allowed": gold["allowed"],
                    "sku": sku,
                    "verdict": "none" if sku is None else "right" if correct else "wrong",
                    "product_seen_emitted": bool(output),
                }
            )

        times, index = sorted(coarse), 0
        for comment in sorted(comments, key=lambda c: c["ts_ms"]):
            while index < len(times) and times[index] * 1000 <= comment["ts_ms"]:
                await consume(times[index])
                index += 1
            cards = [e for e in await store.read("component", 0, 10000) if e["type"] == "card"]
            current, previous = current_at(cards, comment["ts_ms"])
            candidates = [
                {"card_id": c["card_id"], "card_event_id": c["event_id"]} for c in [current, previous] if c
            ]
            label = labels.get(comment["comment_id"])
            target = Attachments.resolve_reference(label, candidates, cards) if label else None
            chosen = next((c for c in [current, previous] if c and c["card_id"] == target), None)
            sku, gold = chosen["sku"] if chosen else None, comment["order"]["product"]
            projections.append(
                {
                    "comment_id": comment["comment_id"],
                    "gold": gold,
                    "saved_reference_kind": ((label or {}).get("order_draft") or {}).get("reference_kind"),
                    "sku": sku,
                    "verdict": "none" if sku is None else "right" if sku == gold else "wrong",
                    "current": current and current["sku"],
                    "previous": previous and previous["sku"],
                    "card_price": chosen and chosen["price"],
                }
            )
        while index < len(times):
            await consume(times[index])
            index += 1
        events = await store.read("component", 0, 10000)
        assert not any(e["type"] in ["model_call", "order", "message_out", "price_proposal"] for e in events)
        assert all(e["price"] is None for e in events if e["type"] == "card")
    return {
        "scope": "Synthetic component adapter; no measured latency/price/confirmation/attachment readiness. "
        "Frozen coarse A; frozen B0 kind/color/state only for other; saved reference kinds from 0742, "
        "gold used only for scoring. Not the changed prompt and not a C5 result.",
        "mode": mode,
        "frames_n": len(frame_rows),
        "comments_n": len(projections),
        "frame_counts": dict(Counter(r["verdict"] for r in frame_rows)),
        "other_frames": dict(
            Counter(r["verdict"] for r in frame_rows if r["stratum"] == "same_colour_other")
        ),
        "comment_counts": dict(Counter(r["verdict"] for r in projections)),
        "frames": frame_rows,
        "comments": projections,
        "card_events": sum(e["type"] == "card" for e in events),
        "model_calls": 0,
        "order_events": 0,
        "source_hashes": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                frozen / "model-compare-calls.jsonl",
                frozen / "model-compare-score.json",
                events_path,
                stream_path,
                catalog_path,
            ]
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", type=Path, required=True)
    parser.add_argument("--mode", choices=["A", "kind"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(replay(args.frozen, args.mode))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ["frames", "comments", "source_hashes"]}))


if __name__ == "__main__":
    main()
