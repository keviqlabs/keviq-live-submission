"""Read-only diagnostic details: frame bounds, digit-phone hides, per-item cards, receipt image hashes.

Uses the existing timing export and ledger-v2 export. Only identifiers and numbers are emitted;
comment text, speech, image bytes and database credentials are never written to the output.
"""

import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path

import httpx

from measure import c5, core
from measure.c5_live import read_all
from measure.c5_score import spans
from worker.brain.shield import scan


def details(events, run_file, timing, ledger):
    frames = [e for e in events if e["type"] == "frame"]
    cards = [e for e in events if e["type"] == "card"]
    seen = [e for e in events if e["type"] == "product_seen"]
    first = {}
    for card in cards:
        if card["card_id"] not in first or card["version"] < first[card["card_id"]]["version"]:
            first[card["card_id"]] = card
    if not all(card["version"] == 1 for card in first.values()):
        raise ValueError("A card is missing version 1")
    if len(first) != timing["loops"][0]["cards"]["opened"]:
        raise ValueError("The timing export omitted a card")
    phones = []
    for comment in events:
        if comment["type"] != "comment" or "phone_number" not in scan(comment["text"]):
            continue
        hidden = sorted(
            [
                e
                for e in events
                if e["type"] == "comment_labeled"
                and e["comment_id"] == comment["comment_id"]
                and e.get("action") == "hide"
            ],
            key=core.when,
        )
        hide = hidden[0] if hidden else None
        phones.append({"comment_id": comment["comment_id"], "comment_ingested_ms": comment["ingested_ms"],
                       "first_hide_event_id": hide and hide["event_id"],
                       "first_hide_ingested_ms": hide and hide["ingested_ms"],
                       "delay_s": None if hide is None else (hide["ingested_ms"] - comment["ingested_ms"]) / 1000})  # fmt: skip
    windows = json.loads((c5.C5 / "windows.json").read_text())
    t0 = run_file["loops"][0]["t0_ms"]
    items = []
    for item, (start, end, _) in zip(timing["loops"][0]["items"], spans(windows), strict=True):
        hits = sorted(
            [e for e in seen if t0 + start <= e["frame_ts_ms"] < t0 + end], key=lambda e: e["frame_ts_ms"]
        )
        opened = [
            c
            for c in first.values()
            if t0 + start <= (c["provenance"].get("frame_ts_ms") or c["ts_ms"]) < t0 + end
        ]
        causes = [
            c
            for c in cards
            if c["status"] != "retired" and any(e["event_id"] in c.get("caused_by", []) for e in hits)
        ]
        row = {
            **item,
            "new_version1_cards": [c["card_id"] for c in opened],
            "card_consequences": [
                {k: c[k] for k in ("card_id", "version", "sku", "event_id")} for c in causes
            ],
        }
        if hits:
            hit = hits[0]
            row["first_seen"] = {
                **item["first_seen"],
                "color": (hit.get("attributes") or {}).get("color"),
                "ingested_after_gold_s": round((hit["ingested_ms"] - t0) / 1000 - item["mark_s"], 6),
            }
        row["no_new_card_reason"] = (
            None
            if opened
            else "no_product_seen_event_in_gold_span"
            if not hits
            else "updated_existing_open_card"
            if causes
            else "UNPROVEN: detection without a stored card consequence"
        )
        items.append(row)
    by_version = {(c["card_id"], c["version"]): c for c in cards}
    confirmed = {
        e["order_id"]: e
        for e in events
        if e["type"] == "order" and e["state"] == "CONFIRMED" and e.get("receipt")
    }
    receipt_rows = []
    for oid, order in confirmed.items():
        receipt = order["receipt"]
        line = order["lines"][0]
        version = receipt["card_version"]
        card = by_version.get((line["card_id"], version))
        url = receipt.get("image_url") or ""
        png = base64.b64decode(url.split(",", 1)[1], validate=True) if url.startswith("data:image/") else b""
        valid_png = png.startswith(b"\x89PNG\r\n\x1a\n")
        receipt_rows.append(
            {
                "order_id": oid,
                "order_event_id": order["event_id"],
                "card_id": line["card_id"],
                "card_version": version,
                "card_version_exists": card is not None,
                "image_matches_cited_version": bool(card and png and card.get("image_url") == url),
                "image_is_valid_png": valid_png,
                "image_bytes": len(png),
                "image_sha256": hashlib.sha256(png).hexdigest() if png else None,
            }
        )
    progress = run_file["loops"][0]["worker_progress"]
    calls = ledger["by_purpose"]["product_seen"]
    successful = calls["n"] - calls["not_ok"]
    return {
        "run": run_file["run"],
        "source_sha": run_file["sha"],
        "frames": len(frames),
        "skipped": progress["skipped"],
        "stale": progress["stale"],
        "successful_product_seen_model_calls": successful,
        "failed_lower_bound": len(frames) - progress["skipped"] - progress["stale"] - successful,
        "failed_upper_bound": progress["errors"],
        "counts_are_lane_exact": False,
        "all_frames_have_outcome": all(
            b["status"] == "SETTLED"
            and all(b[k] == 0 for k in ("pending", "running", "queued"))
            and b["done_seq"] == b["head_seq"]
            for b in run_file["loops"][0]["barrier"]
        ),
        "digit_phones": {
            "n": len(phones),
            "rows": phones,
            "max_s": max((x["delay_s"] for x in phones if x["delay_s"] is not None), default=None),
            "pass": bool(phones) and all(x["delay_s"] is not None and x["delay_s"] <= 5 for x in phones),
        },
        "cards": timing["loops"][0]["cards"],
        "every_card_has_version1": True,
        "items": items,
        "receipt_live_image_check": {
            "rows": receipt_rows,
            "n": len(receipt_rows),
            "passed": sum(r["image_matches_cited_version"] and r["image_is_valid_png"] for r in receipt_rows),
            "scope": "Image bytes exist in the receipt and match the cited version; no visual-quality claim",
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--run-json", required=True)
    parser.add_argument("--timing-json", required=True)
    parser.add_argument("--ledger-json", required=True)
    args = parser.parse_args()
    run_file = json.loads(Path(args.run_json).read_text())["run_file"]
    if len(run_file["loops"]) != 1:
        raise SystemExit("This diagnostic export requires exactly one loop")
    timing = json.loads(Path(args.timing_json).read_text())
    ledger = json.loads(Path(args.ledger_json).read_text())
    if timing["run"] != run_file["run"] or ledger["run"] != run_file["run"]:
        raise SystemExit("Exports belong to different runs")

    async def fetch():
        async with httpx.AsyncClient(base_url=args.base_url, timeout=120) as http:
            return await read_all(http, run_file["session_id"])

    print(json.dumps(details(asyncio.run(fetch()), run_file, timing, ledger), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
