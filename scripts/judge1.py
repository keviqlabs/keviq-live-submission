"""The judge1 session: demo1 with product pictures that this repository draws itself (scripts/judge_draw.py).

    uv run python scripts/judge1.py build                  # writes data/sessions/judge1/{events.jsonl,gold.json}
    uv run python scripts/judge1.py video [--out DIR]      # draws a 64 s video of the four products (needs ffmpeg)
    uv run python scripts/judge1.py feed --session ID      # posts judge1's comments and speech on that video's clock

build: the events and gold labels of data/sessions/demo1, unchanged except that every card and receipt picture
(`sim://sam/card-*-v2.png` in demo1, which the console cannot show) becomes a `data:image/png` URL of the drawn
product. `make replay SESSION=judge1` then shows cards and receipts with pictures, with no model and no key.

video: one still picture per product on a table, in demo1's order and timing (sneaker 0-20 s, bag 20-40 s, black
boot 40-42 s, brown boot 42-64 s), no sound, written to data/local/judge1/judge1.mp4 (gitignored; it is rebuilt
from this script, so it is never committed).

feed: waits for the first `frame` event of the session that `make media` plays the video into, then posts
judge1's viewer comments and the seller's speech lines at their own times on that clock (the video has no sound,
so the speech is typed in). Product sightings, cards, orders and Messenger replies are not posted: with a model
key the workers make them from the frames; answer an order with the console's simulated customer buttons.
"""

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import judge_draw  # noqa: E402

from app.contracts import now_ms  # noqa: E402
from connectors.sim.replay import load, plan  # noqa: E402

SRC = ROOT / "data" / "sessions" / "demo1"
DST = ROOT / "data" / "sessions" / "judge1"
CARD_SKU = {"card-a": "GIAY-01", "card-b": "TUI-03", "card-c": "BOOT-02", "card-d": "BOOT-05"}
TIMELINE = (("GIAY-01", 20), ("TUI-03", 20), ("BOOT-02", 2), ("BOOT-05", 22))  # seconds on screen, in order
FED = ("comment", "speech")


def picture(sku: str) -> str:
    return "data:image/png;base64," + base64.b64encode(judge_draw.product_png(sku)).decode()


def swap(url: str | None, pictures: dict[str, str]) -> str | None:
    """sim://sam/card-b-v2.png -> the drawn picture of card-b's product; anything else unchanged."""
    if not url or not url.startswith("sim://sam/"):
        return url
    card = url.removeprefix("sim://sam/").rsplit("-v", 1)[0]
    return pictures[CARD_SKU[card]]


def build(_args) -> int:
    pictures = {sku: picture(sku) for sku in judge_draw.SKUS}
    lines, swapped = [], 0
    for line in (SRC / "events.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        e["session_id"] = "judge1"
        for holder in (e, e.get("receipt") or {}):
            if "image_url" in holder and holder["image_url"] != swap(holder["image_url"], pictures):
                holder["image_url"] = swap(holder["image_url"], pictures)
                swapped += 1
        lines.append(json.dumps(e, ensure_ascii=False))
    gold = json.loads((SRC / "gold.json").read_text())
    gold["session"] = "judge1"
    gold["note"] = (
        "The labels of demo1, unchanged: judge1 is demo1 with drawn product pictures (scripts/judge1.py)."
    )
    DST.mkdir(parents=True, exist_ok=True)
    (DST / "events.jsonl").write_text("".join(x + "\n" for x in lines))
    (DST / "gold.json").write_text(json.dumps(gold, ensure_ascii=False, indent=1) + "\n")
    print(f"{DST.relative_to(ROOT)}: {len(lines)} events, {swapped} pictures drawn in, gold.json copied")
    return 0


def video(args) -> int:
    if not shutil.which("ffmpeg"):
        raise SystemExit(
            "ffmpeg not found: install it (it is needed only for the video and for `make media`)"
        )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    listing = []
    for sku, seconds in TIMELINE:
        frame = out / f"{sku}-frame.png"
        frame.write_bytes(judge_draw.frame_png(sku))
        listing += [f"file '{frame.name}'", f"duration {seconds}"]
    listing.append(
        listing[-2]
    )  # the concat demuxer drops the last duration unless the last picture comes again
    (out / "judge1.txt").write_text("\n".join(listing) + "\n")
    mp4 = out / "judge1.mp4"
    seconds = sum(s for _, s in TIMELINE)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "judge1.txt",
           "-vf", "fps=30,format=yuv420p", "-t", str(seconds), "-c:v", "mpeg4", "-q:v", "3", mp4.name]  # fmt: skip
    subprocess.run(cmd, cwd=out, check=True)
    print(f"{mp4.relative_to(ROOT) if mp4.is_relative_to(ROOT) else mp4}: {seconds} s, 720x1280, no sound")
    return 0


async def first_frame(http: httpx.AsyncClient, sid: str, wait_s: float) -> int:
    """The session clock's ms at the video's first second, from the earliest frame event."""
    from measure.c5_live import read_all, t0_of

    deadline = time.monotonic() + wait_s
    while True:
        frames = [e for e in await read_all(http, sid) if e["type"] == "frame"]
        if frames:
            return min(t0_of(f) for f in frames)
        if time.monotonic() > deadline:
            raise SystemExit(
                f"no frame in session {sid} after {wait_s:g} s: start `make media SESSION={sid}` first"
            )
        await asyncio.sleep(0.5)


async def feed_run(args) -> int:
    events = [e for e in load(DST / "events.jsonl", fixtures=False) if e["type"] in FED]
    async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(10, read=30)) as http:
        (await http.get("/healthz")).raise_for_status()
        t0 = now_ms() if args.start_now else await first_frame(http, args.session, args.wait)
        schedule = plan(events, args.session, t0)
        print(f"session {args.session}: {len(schedule)} comments and speech lines of judge1, clock started "
              f"{max(now_ms() - t0, 0) / 1000:.1f} s ago", flush=True)  # fmt: skip
        print(f"console: {args.base_url}/console/{args.session}", flush=True)
        for due, ev in schedule:
            await asyncio.sleep(max(due - now_ms(), 0) / 1000)
            (await http.post(f"/api/sessions/{args.session}/events", json=[ev])).raise_for_status()
    print(f"done: {len(schedule)} events posted")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="judge1")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="write data/sessions/judge1 from demo1 and the drawn pictures")
    v = sub.add_parser("video", help="draw the 64 s video (needs ffmpeg)")
    v.add_argument("--out", default=str(ROOT / "data" / "local" / "judge1"))
    f = sub.add_parser("feed", help="post judge1's comments and speech on the video's clock")
    f.add_argument("--session", required=True, help="the app session `make media` plays the video into")
    f.add_argument("--base-url", default="http://127.0.0.1:8000")
    f.add_argument(
        "--wait", type=float, default=120.0, help="seconds to wait for the first frame (default 120)"
    )
    f.add_argument(
        "--start-now", action="store_true", help="start the clock now instead of at the first frame"
    )
    args = p.parse_args(argv)
    if args.cmd == "build":
        return build(args)
    if args.cmd == "video":
        return video(args)
    return asyncio.run(feed_run(args))


if __name__ == "__main__":
    sys.exit(main())
