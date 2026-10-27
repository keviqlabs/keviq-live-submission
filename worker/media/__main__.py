"""python -m worker.media --session demo1 [--target ID] [--video PATH] [--speed 1] [--seconds N] [--no-voice]

Plays a local recording (data/local/<session>/, never committed) into the app on the session clock: one
`frame` event per second (JPEG under data/local/<session>/frames/, its path in the event) and, unless
--no-voice, the audio through Voice Transcribe as final `speech` events. A running `python -m worker` turns
frames into product_seen and cards. Frame timing (C8): received = the frame's moment on the session clock,
infer = the ffmpeg cut, so "frame extraction" is measured per frame.
"""

import argparse
import asyncio
import logging
import time
from pathlib import Path

import httpx

from app.contracts import make_event, now_ms
from app.settings import ROOT
from worker.brain.card import keywords
from worker.media import extract
from worker.voice.transcribe import transcribe

log = logging.getLogger("worker.media")


class Poster:
    """Posts root events (frame, speech, model_call) to one session; the app is the only writer to the store."""

    def __init__(self, base_url: str, target: str, http: httpx.AsyncClient | None = None):
        self.target = target
        self.http = http or httpx.AsyncClient(base_url=base_url, timeout=10)

    async def post(self, partial: dict) -> dict:
        p = dict(partial)
        etype = p.pop("type")
        event = make_event(etype, self.target, p.pop("ts_ms"), "replay", **p)
        (await self.http.post(f"/api/sessions/{self.target}/events", json=[event])).raise_for_status()
        return event


async def play_frames(
    video: Path, folder: Path, poster: Poster, t0: int, speed: float, seconds: float | None
) -> int:
    total = extract.duration_s(video)
    total = min(total, seconds) if seconds else total
    posted = 0
    for i in range(int(total)):
        due = t0 + int(i * 1000 / speed)
        await asyncio.sleep(max(0, due - now_ms()) / 1000)
        out = folder / f"f_{i:05d}.jpg"
        start = now_ms()
        try:
            jpeg = await extract.frame_at(video, float(i), out)
        except RuntimeError as exc:
            log.warning("frame %d skipped: %s", i, exc)
            continue
        end = now_ms()
        width, height = extract.jpeg_size(jpeg)
        path = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
        timing = {"received_ms": due, "infer_start_ms": start, "infer_end_ms": end, "emitted_ms": now_ms()}
        await poster.post(
            {
                "type": "frame",
                "ts_ms": due,
                "jpeg_path": str(path),
                "width": width,
                "height": height,
                "timing": timing,
            }
        )
        posted += 1
    return posted


async def play_voice(
    video: Path, poster: Poster, base_url: str, t0: int, speed: float, seconds: float | None, lang: str
) -> int:
    from meta.client import MetaClient
    from meta.meter import Meter, http_spend
    from meta.transport import HttpTransport

    try:
        transport = HttpTransport()
    except RuntimeError as exc:
        log.warning("voice skipped: %s", exc)
        return 0

    async def emit(fields: dict) -> None:
        await poster.post({"type": "model_call", "ts_ms": now_ms(), **fields})

    client = MetaClient(transport, Meter(http_spend(base_url)), emit)
    pcm = extract.pcm_chunks(video, speed=speed, seconds=seconds)
    return await transcribe(client, pcm, keywords(), lang, lambda pos: t0 + int(pos / speed), poster.post)


async def play(args) -> dict:
    video = Path(args.video) if args.video else extract.recording(args.session)
    if not video or not video.exists():
        raise SystemExit(
            f"no recording in data/local/{args.session}/ (ask the Founder for one; never commit it)"
        )
    poster = Poster(args.base_url, args.target or args.session)
    folder = ROOT / "data" / "local" / args.session / "frames"
    t0, wall = now_ms(), time.monotonic()
    jobs = [play_frames(video, folder, poster, t0, args.speed, args.seconds)]
    if not args.no_voice:
        jobs.append(play_voice(video, poster, args.base_url, t0, args.speed, args.seconds, args.lang))
    counts = await asyncio.gather(*jobs)
    return {
        "frames": counts[0],
        "speech": counts[1] if len(counts) > 1 else None,
        "seconds": round(time.monotonic() - wall, 1),
    }


def main(argv=None):
    p = argparse.ArgumentParser(prog="worker.media")
    p.add_argument("--session", required=True, help="folder under data/local/")
    p.add_argument("--target", help="session id in the app (default: --session)")
    p.add_argument("--video", help="recording path (default: first video in data/local/<session>/)")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--seconds", type=float, help="only the first N seconds")
    p.add_argument("--lang", default="vi")
    p.add_argument("--no-voice", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    print(asyncio.run(play(args)))


if __name__ == "__main__":
    main()
