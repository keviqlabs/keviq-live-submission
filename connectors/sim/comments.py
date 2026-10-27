"""Simulated live comments: the 200 hand-labeled comments of data/comments/ over the card timeline.

    python -m connectors.sim.comments [--limit 50] [--speed 4] [--target ID] [--base-url URL]

Cards (data/comments/cards.jsonl) and comments (data/comments/labels.jsonl, gold fields dropped) are posted on
their own ts_ms; a comment with `arrives_ms` is posted late, at that time (C7). Vietnamese and English, with
scammers and fake phone numbers (0900 000 0xx). The labels stream hides contact comments through the
connector's CommentModerator; in the simulator that only logs.
"""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from app.contracts import make_event, now_ms
from app.settings import ROOT
from connectors.sim.replay import plan, target_name

DATA = ROOT / "data" / "comments"
SESSION = "comments200"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def events(limit: int | None = None, data: Path = DATA) -> list[dict]:
    """Card and comment events with relative ts_ms; the first `limit` comments and the cards shown by then."""
    rows = read_jsonl(data / "labels.jsonl")[:limit]
    last = max(r.get("arrives_ms", r["ts_ms"]) for r in rows)
    out = [c for c in read_jsonl(data / "cards.jsonl") if c["ts_ms"] <= last]
    for r in rows:
        e = make_event(
            "comment",
            SESSION,
            r["ts_ms"],
            "sim",
            event_id=r["comment_id"],
            comment_id=r["comment_id"],
            user_ref=r["user_ref"],
            text=r["text"],
            reply_to=None,
        )
        if "arrives_ms" in r:
            e["_replay_at_ms"] = r["arrives_ms"]
        out.append(e)
    return out


async def run(args) -> str:
    target = args.target or target_name(SESSION)
    schedule = plan(events(args.limit), target, now_ms() + 500, args.speed)
    n = sum(e["type"] == "comment" for _, e in schedule)
    print(f"sim comments: {n} comments, {len(schedule) - n} card events -> {target}")
    print(f"console: {args.base_url}/console/{target}")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=10) as http:
        for due, e in schedule:
            await asyncio.sleep(max(due - now_ms(), 0) / 1000)
            r = await http.post(f"/api/sessions/{target}/events", json=[e])
            r.raise_for_status()
    print(f"done: {len(schedule)} events posted")
    return target


def main(argv=None):
    p = argparse.ArgumentParser(prog="sim-comments")
    p.add_argument("--limit", type=int, help="only the first N comments")
    p.add_argument("--target", help="target session id (default comments200-<MMDD-HHMMSS>)")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    asyncio.run(run(p.parse_args(argv)))


if __name__ == "__main__":
    main()
