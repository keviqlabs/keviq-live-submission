"""Per-loop live-path counts of one paid C5 run (HQ 10:20): frames processed / lost, timeouts, model calls by
purpose, and each receipt's image checked against the card version it cites. Ids, sha256 and numbers only.

Reads the app API (GET events, follow=false) and the run's own files (RUN.json, loopK-worker/media.log).

  (repo root) PYTHONPATH=. uv run python c5_live_extra.py --base-url http://127.0.0.1:8000 --out OUT --run RUN > RUN-live-extra.json
"""

import argparse
import asyncio
import base64
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import httpx

FAILED = re.compile(r" handler (\w+) failed on (\S+?):?\s*$")
STAMP = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")


def events(base_url, run):
    """Every event of the session, read the way the runner reads it (measure.c5_live.read_all)."""
    from measure.c5_live import read_all

    async def go():
        async with httpx.AsyncClient(base_url=base_url, timeout=120) as http:
            return await read_all(http, run)

    return asyncio.run(go())


def image_sha(http, url):
    """(sha256, mime, bytes) of the image a URL points at: data: decoded, a path or http URL fetched."""
    url = str(url or "")
    if url.startswith("data:"):
        head, body = url.split(",", 1)
        raw = base64.b64decode(body)
        return hashlib.sha256(raw).hexdigest(), head.split(";")[0][5:], len(raw)
    if url.startswith(("/", "http")):
        r = http.get(url)
        if r.status_code == 200:
            return hashlib.sha256(r.content).hexdigest(), r.headers.get("content-type"), len(r.content)
    return None


def failures(path):
    """One (handler, frame, final exception) per 'handler X failed on Y' block; the block runs to the next stamped line."""
    out, cur = [], None
    for line in path.read_text(errors="replace").splitlines() if path.exists() else []:
        if STAMP.match(line):
            cur = None
            m = FAILED.search(line)
            if m:
                cur = [m.group(1), m.group(2), None]
                out.append(cur)
        elif cur is not None and line.strip() and not line[0].isspace() and not line.startswith("Traceback"):
            cur[2] = line.split(":", 1)[0].strip()
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--run", required=True)
    a = p.parse_args()
    if a.base_url.rstrip("/").endswith(":8001"):
        sys.exit("refusing :8001")
    out = Path(a.out)
    rf = json.loads((out / f"{a.run}.json").read_text())["run_file"]
    loops = [(lp["k"], lp["t0_ms"], lp["end_ms"]) for lp in rf["loops"]]
    http = httpx.Client(base_url=a.base_url, timeout=60)
    evs = events(a.base_url, a.run)

    def loop(ts):
        return next((k for k, t0, end in loops if t0 <= ts < end), None)

    cards = defaultdict(dict)
    for e in evs:
        if e["type"] == "card":
            cards[e["card_id"]][e["version"]] = e
    per = defaultdict(lambda: {"frames": 0, "product_seen": 0, "calls": defaultdict(lambda: [0, 0, 0.0])})
    for e in evs:
        k = loop(e["ts_ms"])
        if e["type"] == "frame":
            per[k]["frames"] += 1
        elif e["type"] == "product_seen":
            per[k]["product_seen"] += 1
        elif e["type"] == "model_call":
            c = per[k]["calls"][e.get("purpose")]
            c[0] += 1
            c[1] += bool(e.get("ok"))
            c[2] += e.get("cost_usd") or 0.0
    by_id = {e["event_id"]: e for e in evs}
    opened = sorted((min(vs.values(), key=lambda c: c["version"])["ts_ms"], cid) for cid, vs in cards.items())

    rows = []
    for k, t0, _ in loops:
        lost = []
        for role in ("worker", "media"):
            for handler, frame, exc in failures(out / f"{a.run}-loop{k}-{role}.log"):
                ts = (by_id.get(frame) or {}).get("ts_ms")
                live = [cid for t, cid in opened if ts is not None and t <= ts]
                nxt = next(((t, cid) for t, cid in opened if ts is not None and t > ts), None)
                lost.append({"role": role, "handler": handler, "frame": frame, "exception": exc,
                             "timeout": "Timeout" in str(exc), "at_ms": ts and ts - t0,
                             "card_live_before": live[-1] if live else None,
                             "next_card_open": nxt and {"card_id": nxt[1], "after_ms": nxt[0] - ts}})  # fmt: skip
        g = per[k]
        rows.append({"loop": k, "frames": g["frames"], "frames_lost": len(lost), "lost": lost,
                     "timeouts": sum(x["timeout"] for x in lost), "product_seen": g["product_seen"],
                     "model_calls": {pp: {"n": v[0], "ok": v[1], "cost_usd": round(v[2], 6)}
                                     for pp, v in sorted(g["calls"].items(), key=lambda kv: str(kv[0]))}})  # fmt: skip

    rc = []
    for e in evs:
        if e["type"] != "order" or e.get("state") != "CONFIRMED":
            continue
        r = e.get("receipt") or {}
        cid = r.get("card_id") or e["lines"][0]["card_id"]
        ver = r.get("card_version")
        card = cards.get(cid, {}).get(ver)
        got, want = image_sha(http, r.get("image_url")), image_sha(http, (card or {}).get("image_url"))
        url = str(r.get("image_url") or "")
        rc.append({"order_id": e["order_id"], "loop": loop(e["ts_ms"]), "card_id": cid, "card_version": ver,
                   "url_kind": url.split(":", 1)[0] if url else None, "mime": got and got[1],
                   "bytes": got and got[2], "image_sha256": got and got[0],
                   "version_image_sha256": want and want[0],
                   "same_as_cited_version": bool(got and want and got[0] == want[0])})  # fmt: skip
    imgs = []
    for cid, vs in sorted(cards.items()):
        for ver, c in sorted(vs.items()):
            got = image_sha(http, c.get("image_url"))
            url = str(c.get("image_url") or "")
            imgs.append({"card_id": cid, "version": ver, "loop": loop(c["ts_ms"]), "url_kind": url.split(":", 1)[0] if url else None,
                         "mime": got and got[1], "bytes": got and got[2], "image_sha256": got and got[0]})  # fmt: skip
    cards_by_loop = defaultdict(lambda: {"opened": 0, "versions": 0, "versions_with_image": 0})
    for t, cid in opened:
        cards_by_loop[loop(t)]["opened"] += 1
    for x in imgs:
        cards_by_loop[x["loop"]]["versions"] += 1
        cards_by_loop[x["loop"]]["versions_with_image"] += x["image_sha256"] is not None
    outside = {pp: v[0] for pp, v in per[None]["calls"].items()}
    print(json.dumps({"run": a.run, "events": len(evs), "loops": rows, "calls_outside_loops": outside,
                      "cards_by_loop": {str(k): v for k, v in sorted(cards_by_loop.items(), key=lambda kv: str(kv[0]))},
                      "card_images": imgs, "receipts": rc}, indent=1))  # fmt: skip


if __name__ == "__main__":
    main()
