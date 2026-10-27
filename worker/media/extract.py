"""Frames (1 fps, JPEG with the long side at most 640 px) and audio (PCM s16le, 24 kHz mono) from a local recording, via ffmpeg.

Recordings live in data/local/<session>/ (gitignored, never committed); frames are written to its frames/ folder.
Each frame is cut on its own (`-ss` before `-i`, a fast seek), so the extraction time per frame is measurable.
"""

import asyncio
import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from app.settings import ROOT

SAMPLE_RATE = 24000
LONG_SIDE = 640  # phone portrait 1080x1920 -> 360x640, landscape 1280x720 -> 640x360 (D-015)
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".m4v")


def recording(session: str) -> Path | None:
    """The first video file in data/local/<session>/, or None."""
    folder = ROOT / "data" / "local" / session
    files = (
        sorted(p for p in folder.glob("*") if p.suffix.lower() in VIDEO_SUFFIXES) if folder.is_dir() else []
    )
    return files[0] if files else None


def duration_s(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def jpeg_size(data: bytes) -> tuple[int, int]:
    """(width, height) from the JPEG start-of-frame marker."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker, length = data[i + 1], int.from_bytes(data[i + 2 : i + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return int.from_bytes(data[i + 7 : i + 9], "big"), int.from_bytes(data[i + 5 : i + 7], "big")
        i += 2 + length
    raise ValueError("not a JPEG or no start-of-frame marker")


async def frame_at(video: Path, t_s: float, out: Path, long_side: int = LONG_SIDE) -> bytes:
    """One JPEG at t_s seconds, long side scaled to `long_side` px (never upscaled); written to `out` and returned."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-ss", f"{t_s:.3f}", "-i", str(video), "-frames:v", "1",
        "-vf", f"scale='min({long_side},iw)':'min({long_side},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2", "-q:v", "4", "-c:v", "mjpeg", "-f", "image2pipe", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    jpeg, err = await proc.communicate()
    if proc.returncode != 0 or not jpeg:
        raise RuntimeError(f"ffmpeg frame at {t_s:.1f}s failed: {err.decode(errors='replace')[-300:]}")
    await asyncio.to_thread(_save, out, jpeg)
    return jpeg


def _save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def pcm_chunks(
    video: Path, speed: float = 1.0, chunk_ms: int = 100, seconds: float | None = None
) -> AsyncIterator[bytes]:
    """PCM16 mono 24 kHz in `chunk_ms` pieces, paced at `speed` x real time (speed <= 0: as fast as read)."""
    limit = ["-t", f"{seconds:.3f}"] if seconds else []
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-i", str(video), *limit, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    size = SAMPLE_RATE * 2 * chunk_ms // 1000
    loop = asyncio.get_running_loop()
    t0, n = loop.time(), 0
    try:
        while True:
            try:
                chunk = await proc.stdout.readexactly(size)
            except asyncio.IncompleteReadError as exc:
                chunk = exc.partial
            if not chunk:
                break
            if speed > 0:
                await asyncio.sleep(max(0.0, t0 + n * chunk_ms / 1000 / speed - loop.time()))
            n += 1
            yield chunk
            if len(chunk) < size:
                break
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
