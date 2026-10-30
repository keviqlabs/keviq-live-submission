"""Frozen-observation replay source for measure.c5 --base-url: the product_seen and speech events a stored run
observed (its observations.jsonl), moved onto the clock of the loop being run.

The stored run's clock is its first frame's rule (c5_live.t0_of: frame ts minus the frame index in seconds); every
event keeps its offset from that clock, so a product_seen or a line that the stored run saw 83.2 s in is posted
83.2 s into this loop. Ids follow c5_dry.media_items (<sid>.ps-<k>-<nn>, <sid>.u<k>-<nn>); product_seen is
caused by this run's own frame (c5_live.caused) and the stored caused_by, seq and ingested_ms are dropped. Model
responses are not part of the observations, so only these two media outputs can be replayed; labels stay the
gold replay of the offline path.
"""

import hashlib
import json
import re
from pathlib import Path

FRAME = re.compile(r"f_(\d{5})\.jpg$")
SPEECH_LAG_MS = 5
REPLAYED = ("product_seen", "speech")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def clock(events: list[dict]) -> int:
    """The stored run's t0 by the frame rule; the first frame's own ts when its path carries no index."""
    frames = sorted((e for e in events if e["type"] == "frame"), key=lambda e: e["ts_ms"])
    if not frames:
        raise ValueError("observations hold no frame event; no clock to move the media onto")
    first = frames[0]
    m = FRAME.search(first.get("jpeg_path") or "")
    return first["ts_ms"] - (int(m.group(1)) * 1000 if m else 0)


def shifted(timing: dict | None, delta: int) -> dict | None:
    return {key: value + delta for key, value in timing.items()} if timing else None


def items(path: Path, sid: str, k: int, t0: int) -> list[tuple[int, dict]]:
    """(post at, event) for the stored product_seen and speech rows of loop k, on its clock t0."""
    from app.contracts import make_event

    events = load(path)
    delta = t0 - clock(events)
    out, n, u = [], 0, 0
    for e in sorted(
        (e for e in events if e["type"] in REPLAYED), key=lambda e: (e["ts_ms"], e.get("seq", 0))
    ):
        ts = e["ts_ms"] + delta
        timing = shifted(e.get("timing"), delta)
        if e["type"] == "product_seen":
            due = (timing or {}).get("emitted_ms", ts)
            out.append((due, make_event("product_seen", sid, ts, "replay", event_id=f"{sid}.ps-{k}-{n:02d}",
                                        timing=timing, category=e["category"], confidence=e["confidence"],
                                        attributes=e["attributes"], is_new=e["is_new"], frame_ts_ms=ts)))  # fmt: skip
            n += 1
        else:
            start, end = e["start_ms"] + delta, e["end_ms"] + delta
            due = (timing or {}).get("emitted_ms", end) + SPEECH_LAG_MS
            out.append((due, make_event("speech", sid, ts, "replay", event_id=f"{sid}.u{k}-{u:02d}",
                                        timing=timing, text=e["text"], final=e["final"], lang=e["lang"],
                                        start_ms=start, end_ms=end)))  # fmt: skip
            u += 1
    return out
