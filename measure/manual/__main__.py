"""python -m measure.manual prepare | serve | score (see README.md)."""

import argparse
import json
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from measure.manual.score import assistant, load_jsonl, score, table
from measure.manual.server import ROOT, create_app, sha256

C5 = ROOT / "data/comments/c5"
RUNS = ROOT / ".scratch/manual-baseline"
SOURCE = ROOT / "data/local/test1/vi-2026-10-27.mp4"
VIDEO = RUNS / "test1-h264.mp4"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
TAKEN = (8000, 8001)  # the app and its test copy
PRACTICE_STOP_MS, PRACTICE_TAIL_MS, RUN_TAIL_MS = 60_000, 10_000, 30_000


def prepare(args) -> int:
    """An H.264 + AAC copy of the recording that a browser plays, 720 px high."""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(args.source), "-vf", "scale=-2:720", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", str(args.out)]  # fmt: skip
    if subprocess.run(cmd).returncode:
        return 1
    probe = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(args.out)]
    print(f"{args.out}: {subprocess.run(probe, capture_output=True, text=True).stdout.strip()} s")
    return 0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(args, parser) -> int:
    if not args.video.is_file():
        parser.error(f"no video at {args.video}; make one with `prepare`")
    if args.port in TAKEN:
        parser.error(f"port {args.port} belongs to the app; pick another or leave --port out")
    if not 0 < args.speed <= 16:
        parser.error("--speed must be above 0 and at most 16")
    import uvicorn

    if args.practice:
        run_dir, stream, tail_ms, stop_ms = (
            RUNS / f"{args.run}-practice",
            C5 / "stream-2.jsonl",
            PRACTICE_TAIL_MS,
            PRACTICE_STOP_MS,
        )
    else:
        run_dir, stream, tail_ms, stop_ms = RUNS / args.run, C5 / "stream-1.jsonl", RUN_TAIL_MS, None
    app = create_app(run_dir, stream, C5 / "catalog.json", args.video, mode="practice" if args.practice else "run",
                     tail_ms=tail_ms, stop_ms=stop_ms, speed=args.speed)  # fmt: skip
    port = args.port or free_port()
    url = f"http://127.0.0.1:{port}/"
    print(f"{'practice' if args.practice else 'run'} {args.run}: {url}  (log: {run_dir})")
    if not args.no_open:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


def score_run(args) -> int:
    run_dir = args.dir or RUNS / args.run
    run = json.loads((run_dir / "run.json").read_text())
    stream = ROOT / run["stream"]["path"]
    if sha256(stream) != run["stream"]["sha256"]:
        print(f"{stream} is not the stream this run handled (sha256 differs)", file=sys.stderr)
        return 1
    try:
        mine = score(
            load_jsonl(run_dir / "actions.jsonl"), load_jsonl(stream), run, allow_speed=args.allow_speed
        )
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    theirs = assistant(args.assistant) if args.assistant else None
    out = {"run": run, "score": mine, "assistant": theirs}
    (run_dir / "score.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    if run["mode"] != "run":
        print(f"mode {run['mode']}: not a result")
    if run.get("speed", 1) != 1:
        print(f"speed {run['speed']:g}: not a result")
    print(table(mine, theirs))
    print(f"written {run_dir / 'score.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m measure.manual")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="make the browser copy of the recording")
    p.add_argument("--source", type=Path, default=SOURCE)
    p.add_argument("--out", type=Path, default=VIDEO)
    s = sub.add_parser("serve", help="open the page for a practice round or the run")
    s.add_argument("--run", required=True)
    s.add_argument("--video", type=Path, default=VIDEO)
    s.add_argument("--practice", action="store_true")
    s.add_argument("--port", type=int, default=0)
    s.add_argument("--speed", type=float, default=1.0, help="for testing the kit only; the scorer refuses it")
    s.add_argument("--no-open", action="store_true")
    c = sub.add_parser("score", help="score a run against the stream's gold")
    where = c.add_mutually_exclusive_group(required=True)
    where.add_argument("--run")
    where.add_argument("--dir", type=Path, help="a run directory elsewhere, e.g. committed evidence")
    c.add_argument("--assistant", type=Path, help="the assistant's score JSON on the same stream")
    c.add_argument("--allow-speed", action="store_true")
    args = parser.parse_args(argv)
    if getattr(args, "run", None) is not None and not NAME.fullmatch(args.run):
        parser.error("--run: lower-case letters, digits and dashes, up to 41 characters")
    if getattr(args, "run", None) and args.run.endswith("-practice"):
        parser.error("--run: a run name may not end in -practice; that is where its practice round is logged")
    if args.cmd == "prepare":
        return prepare(args)
    if args.cmd == "serve":
        return serve(args, parser)
    return score_run(args)


if __name__ == "__main__":
    sys.exit(main())
