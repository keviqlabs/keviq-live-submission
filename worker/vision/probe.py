"""Smoke the three models on a local recording and print raw numbers (n, latency, cost, accuracy).

    python -m worker.vision.probe --session demo1 [--frames 20] [--sam 3] [--voice-seconds 300] [--base-url URL]

Needs the app running (spend caps are read from it, and every call is posted as a model_call event to the
session `probe-<session>`) and MODEL_API_KEY in .env. Spark accuracy needs labels written by a person in
data/local/<session>/labels.json: [{"from_s": 0, "to_s": 19.9, "category": "sneaker"}, ...] ("none" for no
product). Without labels the predictions are printed and accuracy is reported as not measured.
Outputs (frames, SAM crops, summary.json) go to data/local/<session>/probe/, which is never committed.
"""

import argparse
import asyncio
import json
import logging

from app.contracts import now_ms
from app.settings import ROOT
from measure.core import pct
from meta.client import MetaClient
from meta.meter import Meter, http_spend
from meta.transport import HttpTransport
from worker.brain.card import keywords
from worker.media import extract
from worker.media.__main__ import Poster
from worker.vision import sam, spark
from worker.voice.transcribe import transcribe


def label_at(labels: list[dict], t_s: float) -> str | None:
    return next((x["category"] for x in labels if x["from_s"] <= t_s <= x["to_s"]), None)


def stats(values: list[int]) -> dict:
    return {"n": len(values), "p50": pct(values, 50), "p95": pct(values, 95)}


async def probe(args) -> dict:
    video = extract.recording(args.session)
    if not video:
        raise SystemExit(f"no recording in data/local/{args.session}/")
    folder = ROOT / "data" / "local" / args.session
    out = folder / "probe"
    labels_file = folder / "labels.json"
    labels = json.loads(labels_file.read_text()) if labels_file.exists() else []
    poster = Poster(args.base_url, f"probe-{args.session}")
    calls: list[dict] = []

    async def emit(fields: dict) -> None:
        calls.append(fields)
        await poster.post({"type": "model_call", "ts_ms": now_ms(), **fields})

    client = MetaClient(HttpTransport(), Meter(http_spend(args.base_url)), emit)
    duration = extract.duration_s(video)
    report: dict = {"recording_s": round(duration, 1)}

    rows, spark_ms = [], []
    for k in range(args.frames):
        t_s = round(duration * (k + 0.5) / args.frames, 1)
        jpeg = await extract.frame_at(video, t_s, out / f"f_{k:03d}.jpg")
        try:
            res = await spark.read_frame(client, jpeg, None)
            d, ms = res.data, res.latency_ms
        except Exception as exc:  # counted as a wrong answer, not hidden
            d, ms = {"category": "error", "error": str(exc)[:120]}, None
        predicted = d["category"] if d["category"] == "error" or d["has_product"] else "none"
        truth = label_at(labels, t_s)
        rows.append({"t_s": t_s, "predicted": predicted, "color": d.get("color"), "confidence": d.get("confidence"),
                     "label": truth, "ok": None if truth is None else predicted == truth, "ms": ms, "jpeg": jpeg})  # fmt: skip
        if ms is not None:
            spark_ms.append(ms)
        print(f"  spark t={t_s:>7}s -> {predicted:<9} {d.get('color')!s:<6} conf={d.get('confidence')} "
              f"label={truth} {ms} ms")  # fmt: skip
    judged = [r for r in rows if r["ok"] is not None]
    report["spark"] = {
        "frames": len(rows),
        "latency_ms": stats(spark_ms),
        "labelled": len(judged),
        "correct": sum(r["ok"] for r in judged),
        "accuracy": round(sum(r["ok"] for r in judged) / len(judged), 3)
        if judged
        else "not measured: no labels",
    }

    sam_ms, sam_ok = [], 0
    for k, r in enumerate([r for r in rows if r["predicted"] not in ("none", "other", "error")][: args.sam]):
        try:
            res = await sam.cut_out(client, r["jpeg"], r["predicted"])
        except Exception as exc:
            print(f"  sam  t={r['t_s']}s failed: {exc}")
            continue
        if res:
            url, (start, end) = res
            sam_ms.append(end - start)
            sam_ok += 1
            (out / f"cut_{k:02d}.png").write_bytes(__import__("base64").b64decode(url.split(",", 1)[1]))
        print(f"  sam  t={r['t_s']}s -> {'cut-out saved' if res else 'nothing found (see log)'}")
    report["sam"] = {"calls": sum(1 for c in calls if c["model"] == client.models["sam"]), "cut_outs": sam_ok,
                     "latency_ms": stats(sam_ms)}  # fmt: skip

    if args.voice_seconds:
        heard: list[dict] = []

        async def keep(partial: dict) -> None:
            heard.append(partial)

        t0 = now_ms()
        pcm = extract.pcm_chunks(video, speed=1.0, seconds=args.voice_seconds)
        try:
            await transcribe(client, pcm, keywords(), args.lang, lambda pos: t0 + pos, keep)
        except Exception as exc:
            print(f"  voice failed: {type(exc).__name__}: {exc}")
        lag = [h["timing"]["infer_end_ms"] - h["timing"]["infer_start_ms"] for h in heard]
        report["voice"] = {"audio_s": args.voice_seconds, "finals": len(heard), "latency_ms": stats(lag)}
        for h in heard[:5]:
            print(f"  voice {h['start_ms'] - t0:>7} ms: {h['text'][:80]}")

    report["cost_usd"] = round(sum(c["cost_usd"] for c in calls), 6)
    report["calls"] = len(calls)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps({**report, "rows": [{k: v for k, v in r.items() if k != "jpeg"} for r in rows]}, indent=1)
    )
    return report


def main(argv=None):
    p = argparse.ArgumentParser(prog="worker.vision.probe")
    p.add_argument("--session", required=True)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--frames", type=int, default=20)
    p.add_argument("--sam", type=int, default=3)
    p.add_argument("--voice-seconds", type=float, default=0)
    p.add_argument("--lang", default="vi")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    print(json.dumps(asyncio.run(probe(args)), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
