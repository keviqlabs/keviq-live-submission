"""Recent frames per session (JPEG bytes by frame ts_ms), so a card can be cut out of the frame it came from."""

import base64
from collections import OrderedDict
from pathlib import Path

from app.settings import ROOT

KEEP = 180  # 3 minutes at 1 fps, about 10 MB
_CACHE: dict[str, OrderedDict[int, bytes]] = {}


def load(event: dict) -> bytes:
    """JPEG bytes of a frame event: inline base64, or a path (relative paths are from the repo root)."""
    if event.get("jpeg_b64"):
        return base64.b64decode(event["jpeg_b64"])
    path = Path(event["jpeg_path"])
    return (path if path.is_absolute() else ROOT / path).read_bytes()


def put(session: str, ts_ms: int, jpeg: bytes) -> None:
    frames = _CACHE.setdefault(session, OrderedDict())
    frames[ts_ms] = jpeg
    while len(frames) > KEEP:
        frames.popitem(last=False)


def get(session: str, ts_ms: int) -> bytes | None:
    return (_CACHE.get(session) or {}).get(ts_ms)


def data_url(jpeg: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(jpeg).decode()}"
