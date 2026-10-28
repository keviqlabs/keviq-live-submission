"""Paid C5, the 5-minute image rule. Reads the app API only (no ledger, no key).

From the session's first frame, within --window-s (300) there must be both:
  - a model_call with purpose card_cutout and ok true, and
  - a card version whose image_url is a data:image/ URL.
Found: print IMAGE_OK with card_id/version, the image sha256 and the model_call event id; exit 0; the run goes on.
Not found: print IMAGE_MISSING, SIGTERM the runner (python -m measure.c5 ... --run RUN) and its children
(measure.c5_proc ... RUN), SIGKILL after 10 s, print /api/cost?session=RUN (DIAG + cost); exit 1.
Prints ids, sha256 and numbers only, no comment or speech text.

  uv run python paid-image-5min.py --base-url http://127.0.0.1:8000 --run c5-test1x5-1028-HHMM > image-5min.log
"""

import argparse
import base64
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

import httpx


def page(http, run, cursor):
    r = http.get(f"/api/sessions/{run}/events", params={"cursor": cursor, "follow": "false"})
    r.raise_for_status()
    out = []
    for line in r.text.splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[5:].strip()))
    return out


def pids(pattern):
    r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    return [int(p) for p in r.stdout.split() if int(p) != os.getpid()]


def stop(run):
    targets = pids(rf"-m measure\.c5 .*--run {run}") + pids(rf"measure\.c5_proc .*{run}")
    for p in targets:
        try:
            os.kill(p, signal.SIGTERM)
        except ProcessLookupError:
            pass
    time.sleep(10)
    left = [p for p in targets if subprocess.run(["kill", "-0", str(p)], capture_output=True).returncode == 0]
    for p in left:
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return targets, left


def say(**f):
    print(json.dumps({"t": time.strftime("%H:%M:%S"), **f}), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--window-s", type=float, default=300)
    a = p.parse_args()
    if a.base_url.rstrip("/").endswith(":8001"):
        sys.exit("refusing :8001")
    http = httpx.Client(base_url=a.base_url, timeout=30)
    cursor, t0, cut, img = 0, None, None, None
    while True:
        for ev in page(http, a.run, cursor):
            cursor = max(cursor, ev.get("seq") or 0)
            if ev["type"] == "frame" and t0 is None:
                t0 = ev["ts_ms"]
                say(kind="t0", t0_ms=t0, frame=ev["event_id"])
            elif ev["type"] == "model_call" and ev.get("purpose") == "card_cutout" and ev.get("ok") and not cut:
                cut = ev["event_id"]
                say(kind="cutout_call", event_id=cut, model=ev.get("model"), cost_usd=ev.get("cost_usd"))
            elif ev["type"] == "card" and str(ev.get("image_url") or "").startswith("data:image/") and not img:
                raw = base64.b64decode(ev["image_url"].split(",", 1)[1])
                img = {"card_id": ev["card_id"], "version": ev["version"], "event_id": ev["event_id"],
                       "image_sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}  # fmt: skip
                say(kind="card_image", **img)
        if cut and img:
            say(kind="IMAGE_OK", cutout_call=cut, **img)
            return 0
        if t0 is not None and time.time() * 1000 - t0 > a.window_s * 1000:
            say(kind="IMAGE_MISSING", window_s=a.window_s, cutout_call=cut, card_image=img)
            targets, killed = stop(a.run)
            say(kind="stopped", pids=targets, sigkill=killed)
            say(kind="cost", **http.get("/api/cost", params={"session": a.run}).json())
            return 1
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
