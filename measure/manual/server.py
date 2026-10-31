"""The local page for a manual run: the video, the comments as they appear, and a log of every action."""

import hashlib
import json
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from measure.manual.score import ON_COMMENT, appears_ms, load_jsonl

ROOT = Path(__file__).resolve().parents[2]
PAGE = Path(__file__).with_name("page.html")
VN = timezone(timedelta(hours=7))
UNCLEAR = "unclear"
NO_STORE = {"Cache-Control": "no-store"}


class Entry(BaseModel):
    """One line of the action log, as the page sends it."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "start", "order", "ask", "hide", "reply", "pause", "seek", "stall", "hidden", "visible", "end", "lock"
    ]
    video_ms: int = Field(ge=0)
    perf_ms: float = Field(ge=0)
    comment_id: str | None = None
    item: str | None = None
    size: str | None = Field(default=None, max_length=8)
    qty: int | None = Field(default=None, ge=1, le=99)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(path: Path) -> str:
    path = path.resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def sizes(item: dict) -> list[str]:
    """The item's sizes in catalog order; an item without sizes has none."""
    return list(dict.fromkeys(str(v["size"]) for v in item.get("variants", []) if v.get("size") is not None))


def create_app(
    run_dir: Path,
    stream: Path,
    catalog: Path,
    video: Path,
    *,
    mode: str,
    tail_ms: int,
    stop_ms: int | None = None,
    speed: float = 1.0,
) -> FastAPI:
    rows = load_jsonl(stream)
    items = json.loads(catalog.read_text())
    by_sku = {i["sku"]: i for i in items}
    known = {r["comment_id"] for r in rows}
    comments = sorted(
        (
            {
                "comment_id": r["comment_id"],
                "at_ms": appears_ms(r),
                "user_ref": r["user_ref"],
                "text": r["text"],
            }
            for r in rows
        ),
        key=lambda c: (c["at_ms"], c["comment_id"]),
    )
    shop = [{k: i.get(k) for k in ("sku", "name", "price", "currency", "variants")} for i in items]
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "actions.jsonl"
    state = {"seq": 0, "start": False, "end": False, "lock": False}
    # A restarted server keeps the run it had: no second start, no actions after the lock.
    if log_path.exists():
        for e in load_jsonl(log_path):
            state["seq"] += 1
            if e["kind"] in ("start", "end", "lock"):
                state[e["kind"]] = True
    guard = threading.Lock()
    app = FastAPI(title="manual baseline", docs_url=None, redoc_url=None, openapi_url=None)

    def problem(e: Entry) -> str | None:
        if e.kind not in ON_COMMENT:
            if any(v is not None for v in (e.comment_id, e.item, e.size, e.qty)):
                return f"{e.kind} carries no comment, item, size or quantity"
            return None
        if e.comment_id not in known:
            return "unknown comment"
        if e.kind in ("hide", "reply"):
            if any(v is not None for v in (e.item, e.size, e.qty)):
                return f"{e.kind} carries no item, size or quantity"
            return None
        if e.item is not None and e.item != UNCLEAR and e.item not in by_sku:
            return "unknown item"
        if e.kind == "order" and (e.item is None or e.qty is None):
            return "an order needs an item and a quantity"
        if e.kind == "ask" and e.item is None:
            return "an ask needs an item code or unclear"
        if e.size is not None and (e.item not in by_sku or e.size not in sizes(by_sku[e.item])):
            return "the size is not one of the item's sizes"
        return None

    def run_file() -> dict:
        return {
            "mode": mode,
            "stream": {"path": rel(stream), "sha256": sha256(stream), "rows": len(rows)},
            "catalog": {"path": rel(catalog), "sha256": sha256(catalog)},
            "video": {"name": video.name, "bytes": video.stat().st_size, "sha256": sha256(video)},
            "speed": speed,
            "tail_ms": tail_ms,
            "stop_ms": stop_ms,
            "git_head": git("rev-parse", "HEAD"),
            "git_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
            "started_at": datetime.now(VN).isoformat(timespec="seconds"),
        }

    @app.get("/")
    def page() -> HTMLResponse:
        return HTMLResponse(PAGE.read_text(), headers=NO_STORE)

    @app.get("/video")
    def media() -> FileResponse:
        return FileResponse(video, media_type="video/mp4")

    @app.get("/data")
    def data() -> dict:
        return {
            "mode": mode,
            "tail_ms": tail_ms,
            "stop_ms": stop_ms,
            "speed": speed,
            "started": state["start"],
            "locked": state["lock"],
            "comments": comments,
            "catalog": shop,
        }

    @app.post("/log")
    def log(e: Entry) -> dict:
        if why := problem(e):
            raise HTTPException(422, why)
        with guard:
            if e.kind == "start" and state["start"]:
                raise HTTPException(409, "this run has already started; a run is not restarted")
            if e.kind != "start" and not state["start"]:
                raise HTTPException(409, "the run has not started")
            if state["lock"]:
                raise HTTPException(409, "the run is locked")
            if e.kind == "end" and state["end"]:
                raise HTTPException(409, "the video has already ended")
            if e.kind == "lock" and not state["end"]:
                raise HTTPException(409, "the video has not ended")
            line = {
                "seq": state["seq"],
                "server_ms": time.time_ns() // 1_000_000,
                **e.model_dump(exclude_none=True),
            }
            if e.kind == "start":
                (run_dir / "run.json").write_text(json.dumps(run_file(), indent=2, ensure_ascii=False) + "\n")
            with log_path.open("a") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
            state["seq"] += 1
            if e.kind in ("start", "end", "lock"):
                state[e.kind] = True
        return {"seq": line["seq"]}

    return app
