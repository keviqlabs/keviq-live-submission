"""Per-loop timing of one C5 run: when vision first saw each gold item, and how long cards took to open.
Ids and numbers only; no comment text, utterances or media.

Items: the gold spans of data/comments/c5/windows.json by the scorer's own rule (measure.c5_score.spans: from an
item's change band to the next one's). The first product_seen whose frame falls in a span is that item's first sight,
by span, not by category (the rule Run._item and the shop use). after_mark_s = that frame's time minus the gold
change point (from_ms; negative inside the band), emit_lag_s = its ingested_ms minus its frame's time. No
product_seen in the span: never seen.
Cards: per card, the source frame of its first version (provenance.frame_ts_ms, as Run._item) and the ingested_ms
of its first version and of its first live version; lag = ingested_ms minus the source frame's time.

  (repo root) PYTHONPATH=. uv run python c5_timing.py --base-url http://127.0.0.1:8000 --run-json RUN.json > RUN-timing.json
"""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

import httpx

from measure import c5, core
from measure.c5_live import read_all
from measure.c5_score import spans

OPEN_S = 10  # A card opens within 10 s of its source frame


def events(base_url, run):
    async def go():
        async with httpx.AsyncClient(base_url=base_url, timeout=120) as http:
            return await read_all(http, run)

    return asyncio.run(go())


def lags(values):
    vals = [v for v in values if v is not None]
    return {"n": len(vals), "p50_s": core.pct(vals, 50), "max_s": max(vals) if vals else None,
            f"le_{OPEN_S}s": sum(v <= OPEN_S for v in vals)}  # fmt: skip


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--run-json", required=True, help="the runner's RUN.json (its loops' t0_ms / end_ms)")
    a = p.parse_args(argv)
    d = json.loads(Path(a.run_json).read_text())
    run_file = d.get("run_file", d)
    windows = json.loads((c5.C5 / "windows.json").read_text())
    evs = events(a.base_url, run_file["session_id"])
    seen = sorted(
        (e for e in evs if e["type"] == "product_seen"), key=lambda e: e.get("frame_ts_ms") or e["ts_ms"]
    )
    frames = [e for e in evs if e["type"] == "frame"]
    cards = {}
    for e in sorted((e for e in evs if e["type"] == "card"), key=lambda e: e["version"]):
        cards.setdefault(e["card_id"], []).append(e)
    out = {"run": run_file["run"], "sha": run_file.get("sha"), "open_s": OPEN_S, "loops": []}
    for lp in run_file["loops"]:
        t0, end = lp["t0_ms"], lp["end_ms"]
        items, segs = [], [s for s in windows["segments"] if s["product"]]
        for (start, stop, item), seg in zip(spans(windows), segs, strict=True):
            hit = next(
                (e for e in seen if t0 + start <= (e.get("frame_ts_ms") or e["ts_ms"]) < t0 + stop), None
            )
            at = hit and (hit.get("frame_ts_ms") or hit["ts_ms"])
            items.append({"item": item, "mark_s": seg["from_ms"] / 1000, "span_s": [start / 1000, stop / 1000],
                          "first_seen": hit and {"event_id": hit["event_id"], "category": hit.get("category"),
                                                 "after_mark_s": (at - t0 - seg["from_ms"]) / 1000,
                                                 "emit_lag_s": (hit["ingested_ms"] - at) / 1000}})  # fmt: skip
        rows = []
        for cid, vs in cards.items():
            src = vs[0]["provenance"].get("frame_ts_ms") or vs[0]["ts_ms"]
            if not t0 <= src < end:
                continue
            live = next((v for v in vs if v["status"] == "live"), None)
            rows.append({"card_id": cid, "sku": vs[0].get("sku"), "source_s": (src - t0) / 1000,
                         "first_version_lag_s": (vs[0]["ingested_ms"] - src) / 1000,
                         "first_live_lag_s": live and (live["ingested_ms"] - src) / 1000})  # fmt: skip
        sizes = Counter(f"{f.get('width')}x{f.get('height')}" for f in frames if t0 <= f["ts_ms"] < end)
        out["loops"].append({
            "k": lp["k"], "frame_sizes": dict(sizes), "items": items,
            "never_seen": [i["item"] for i in items if not i["first_seen"]],
            "cards": {"opened": len(rows), "first_version": lags([r["first_version_lag_s"] for r in rows]),
                      "first_live": lags([r["first_live_lag_s"] for r in rows]), "rows": rows},
        })  # fmt: skip
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
