"""Posts one loop of the C5 comment stream into a session, on the clock of the recording `make media` plays there.

    uv run python scripts/c5_comments.py --session ID [--loop 1] [--seconds N] [--base-url URL] [--start-now]

`data/comments/c5/stream-<loop>.jsonl` was written for the test1 recording (351.5 s): a comment about an item sits
inside the span of the video where that item is shown (`data/comments/c5/windows.json`). The script waits for the
session's first `frame` event, takes the recording's start from it and posts each comment at start + its own
time (a late comment at its `arrives_ms`), so start it together with `make media`. With `--start-now` it starts
the clock at once and needs no frame (for a check without a recording; the comments then match nothing on screen).

Only what a viewer would type is sent (comment id, pseudonym, text); the gold fields (label, order,
expected_action) stay in the file. The customers' Messenger replies are not sent: answer an order with the
console's simulated customer buttons. Nothing here calls a model.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.contracts import now_ms  # noqa: E402
from measure import c5  # noqa: E402
from measure.c5_live import post_comments, read_all, t0_of  # noqa: E402


async def recording_start(http: httpx.AsyncClient, sid: str, wait_s: float) -> int:
    """The session clock's ms at the recording's first second, from the earliest frame event."""
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


async def run(args) -> int:
    rows, _ = c5.streams(args.loop)
    if args.seconds:
        rows = [r for r in rows if r.get("arrives_ms", r["ts_ms"]) < args.seconds * 1000]
    async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(10, read=30)) as http:
        (await http.get("/healthz")).raise_for_status()
        t0 = now_ms() if args.start_now else await recording_start(http, args.session, args.wait)
        late = max(now_ms() - t0, 0)
        print(f"session {args.session}: {len(rows)} comments of stream-{args.loop}, clock started "
              f"{late / 1000:.1f} s ago", flush=True)  # fmt: skip
        print(f"console: {args.base_url}/console/{args.session}", flush=True)
        worst = await post_comments(http, args.session, c5.comment_schedule(rows, args.session, t0))
    print(f"done: {len(rows)} comments posted, latest {worst} ms after its time")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="c5_comments")
    p.add_argument("--session", required=True, help="the app session the recording plays into")
    p.add_argument("--loop", type=int, default=1, choices=range(1, 6), help="stream-<loop>.jsonl (default 1)")
    p.add_argument("--seconds", type=float, help="only the comments of the first N seconds")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument(
        "--wait", type=float, default=120.0, help="seconds to wait for the first frame (default 120)"
    )
    p.add_argument(
        "--start-now", action="store_true", help="start the clock now instead of at the first frame"
    )
    return asyncio.run(run(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
