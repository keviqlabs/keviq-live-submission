"""Replay a recorded session into the app on its own clock.

    python -m connectors.sim.replay --session demo1 [--speed 4] [--no-fixtures] [--base-url URL]

Each JSONL line is one event with relative ts_ms (0 = session start). A line may carry `_replay_at_ms`, the
arrival time when it differs from ts_ms (late comments, C7); the key is stripped before posting. Events whose
event_id starts with `fx-` are hand-written stand-ins for worker output so the console has something to show
before the streams land; `--no-fixtures` drops them so the real workers produce those events instead.
"""

import argparse
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.contracts import now_ms
from app.settings import ROOT

TIME_FIELDS = ("ts_ms", "frame_ts_ms", "start_ms", "end_ms")
VN = timezone(timedelta(hours=7))


def load(path: Path, fixtures: bool = True) -> list[dict]:
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [e for e in events if fixtures or not e["event_id"].startswith("fx-")]


def plan(events: list[dict], target: str, start_ms: int, speed: float = 1.0) -> list[tuple[int, dict]]:
    """Return [(send_at_epoch_ms, event)] with ids namespaced to `target` and times rebased to start_ms."""

    def at(rel: int) -> int:
        return start_ms + int(rel / speed)

    def rid(eid: str) -> str:
        return f"{target}.{eid}"

    out = []
    for src in events:
        e = json.loads(json.dumps(src))
        send_rel = e.pop("_replay_at_ms", e["ts_ms"])
        e["session_id"], e["event_id"] = target, rid(e["event_id"])
        for field in TIME_FIELDS:
            if isinstance(e.get(field), int):
                e[field] = at(e[field])
        if "caused_by" in e:
            e["caused_by"] = [rid(x) for x in e["caused_by"]]
        prov = e.get("provenance")
        if prov:
            if isinstance(prov.get("frame_ts_ms"), int):
                prov["frame_ts_ms"] = at(prov["frame_ts_ms"])
            prov["speech_event_ids"] = [rid(x) for x in prov.get("speech_event_ids", [])]
        out.append((at(send_rel), e))
    out.sort(key=lambda p: p[0])
    return out


def target_name(session: str, now: float | None = None) -> str:
    return f"{session}-{datetime.fromtimestamp(now or time.time(), VN):%m%d-%H%M%S}"


async def run(args) -> str:
    path = Path(args.file) if args.file else ROOT / "data" / "sessions" / args.session / "events.jsonl"
    target = args.target or target_name(args.session)
    schedule = plan(load(path, not args.no_fixtures), target, now_ms() + 500, args.speed)
    print(
        f"replay {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}: {len(schedule)} events -> {target}"
    )
    print(f"console: {args.base_url}/console/{target}")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=10) as http:
        i = 0
        while i < len(schedule):
            due = schedule[i][0]
            batch = [e for t, e in schedule[i:] if t <= due + 20]
            i += len(batch)
            await asyncio.sleep(max(due - now_ms(), 0) / 1000)
            r = await http.post(f"/api/sessions/{target}/events", json=batch)
            r.raise_for_status()
    print(f"done: {len(schedule)} events posted")
    return target


def main(argv=None):
    p = argparse.ArgumentParser(prog="replay")
    p.add_argument("--session", default="demo1")
    p.add_argument("--file", help="events.jsonl (default data/sessions/<session>/events.jsonl)")
    p.add_argument("--target", help="target session id (default <session>-<MMDD-HHMMSS>)")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--no-fixtures", action="store_true")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    asyncio.run(run(p.parse_args(argv)))


if __name__ == "__main__":
    main()
