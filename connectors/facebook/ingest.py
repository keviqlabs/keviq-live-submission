"""Append the comments of one Facebook live video to an app session (Level 2, read-only).

    python -m connectors.facebook.ingest --session ID [--base-url URL] [--poll-seconds S] [--once]

Configuration comes from the environment (a repo-root .env is loaded by app.settings): FACEBOOK_PAGE_TOKEN
and FACEBOOK_LIVE_VIDEO_ID, optionally FACEBOOK_GRAPH_VERSION and FACEBOOK_POLL_SECONDS. Each comment becomes
a `comment` event with source `facebook`, posted to POST /api/sessions/{id}/events exactly like the simulator:
the app stays the only writer. Only GET requests reach Facebook; nothing is sent and nothing is hidden.
"""

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from app.contracts import make_event, now_ms
from connectors.facebook.comments import DEFAULT_GRAPH_VERSION, FacebookCommentSource, GraphError, redact

SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
REQUIRED = ("FACEBOOK_PAGE_TOKEN", "FACEBOOK_LIVE_VIDEO_ID")


@dataclass(frozen=True, repr=False)
class Config:
    token: str
    live_video_id: str
    graph_version: str = DEFAULT_GRAPH_VERSION
    poll_seconds: float = 2.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Config":
        """Names only in messages, never values."""
        missing = [k for k in REQUIRED if not env.get(k)]
        if missing:
            raise SystemExit(
                f"missing {', '.join(missing)}: set them in .env (fake values in deploy/.env.example)"
            )
        return cls(
            env["FACEBOOK_PAGE_TOKEN"],
            env["FACEBOOK_LIVE_VIDEO_ID"],
            env.get("FACEBOOK_GRAPH_VERSION") or DEFAULT_GRAPH_VERSION,
            float(env.get("FACEBOOK_POLL_SECONDS") or 2),
        )

    def __repr__(self) -> str:
        return f"Config(graph_version={self.graph_version!r}, poll_seconds={self.poll_seconds!r})"


def event(session_id: str, row: dict, received_ms: int) -> dict:
    """The contract event for one comment row; event_id = comment_id keeps re-polls idempotent in the app."""
    return make_event(
        "comment",
        session_id,
        row["ts_ms"],
        "facebook",
        event_id=row["comment_id"],
        comment_id=row["comment_id"],
        user_ref=row["user_ref"],
        text=row["text"],
        reply_to=row["reply_to"],
        timing={"received_ms": received_ms, "emitted_ms": now_ms()},
    )


async def run(
    source, http: httpx.AsyncClient, session_id: str, *, max_polls: int | None = None, out=print
) -> int:
    """Forward every comment the source yields to the app; returns the number of events posted."""
    if not SESSION_PATTERN.match(session_id):
        raise SystemExit(f"session id {session_id!r} is not valid (letters, digits, _ and - up to 64)")
    posted = 0
    async for row in source.comments(max_polls=max_polls):
        r = await http.post(f"/api/sessions/{session_id}/events", json=[event(session_id, row, now_ms())])
        r.raise_for_status()
        posted += 1
        if posted % 50 == 0:
            out(f"live-ingest: {posted} comments -> {session_id}")
    return posted


async def amain(args) -> int:
    config = Config.from_env()
    poll_seconds = args.poll_seconds or config.poll_seconds
    print(f"live-ingest: read-only, one poll every {poll_seconds:g}s -> session {args.session}")
    print(f"console: {args.base_url}/console/{args.session}")
    source = FacebookCommentSource(
        config.live_video_id, config.token, graph_version=config.graph_version, poll_seconds=poll_seconds
    )
    async with source, httpx.AsyncClient(base_url=args.base_url, timeout=10) as http:
        try:
            posted = await run(source, http, args.session, max_polls=1 if args.once else None)
        except GraphError as exc:
            print(f"live-ingest stopped: {redact(str(exc), config.token)}", file=sys.stderr)
            return 1
    print(f"done: {posted} comments posted")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="live-ingest", description="read-only Facebook live comments -> app session"
    )
    p.add_argument("--session", required=True, help="target session id in the app")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--poll-seconds", type=float, help="override FACEBOOK_POLL_SECONDS")
    p.add_argument("--once", action="store_true", help="one poll, then exit (checks token and video id)")
    try:
        return asyncio.run(amain(p.parse_args(argv)))
    except KeyboardInterrupt:
        print("live-ingest: stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
