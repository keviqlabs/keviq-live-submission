"""SAM 3.1 card image: one short English noun phrase per call, the frame as a data URL.

Reply verified on 2026-10-27 (D-019): a Responses object whose output text holds tokens
`<|box;x1=..;y1=..;x2=..;y2=..;w=<frameW>;h=<frameH>|><|mask;x=0;y=0;data=<H>,<W>,<marker><base85>|>`.
The box is in source pixels, corners inclusive. The mask encoding is not specified in the docs we may read
(dev.meta.ai/docs/sam/reading-segmentation) and we could not decode it, so the card image is the frame cropped
to SAM's first box: real pixels, background not removed, nothing retouched or generated. The result is a PNG
data URL at most 360 px wide; when the reply has no box the card keeps image_url null.
"""

import asyncio
import logging
import re
import tempfile
from pathlib import Path

from worker.media.extract import jpeg_size
from worker.vision import frames

log = logging.getLogger("worker.sam")
CARD_WIDTH = 360
BOX = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)[;|]")
NOUNS = {
    "bag": "handbag",
    "belt": "belt",
    "boot": "boot",
    "dress": "dress",
    "hat": "hat",
    "heels": "high heel shoe",
    "jacket": "jacket",
    "jeans": "jeans",
    "loafer": "loafer",
    "sandal": "sandal",
    "shirt": "shirt",
    "skirt": "skirt",
    "sneaker": "sneaker",
    "sweater": "sweater",
    "trousers": "trousers",
    "tshirt": "t-shirt",
}


def noun(category: str) -> str:
    return NOUNS.get(category, category.replace("_", " "))


def texts(node) -> list[str]:
    """Every string anywhere in the reply."""
    if isinstance(node, dict):
        return [x for v in node.values() for x in texts(v)]
    if isinstance(node, list):
        return [x for v in node for x in texts(v)]
    return [node] if isinstance(node, str) else []


def box(reply) -> tuple[float, float, float, float] | None:
    """SAM's first box as fractions of the frame (left, top, right, bottom), or None."""
    for text in texts(reply):
        if m := BOX.search(text):
            x1, y1, x2, y2, w, h = map(int, m.groups())
            return x1 / w, y1 / h, (x2 + 1) / w, (y2 + 1) / h
    return None


def shape(node, depth: int = 0):
    """Keys and value types of a reply, for logs (no image data, no text content)."""
    if isinstance(node, dict) and depth < 4:
        return {k: shape(v, depth + 1) for k, v in list(node.items())[:12]}
    if isinstance(node, list) and depth < 4:
        return [shape(node[0], depth + 1), f"x{len(node)}"] if node else []
    return type(node).__name__


async def _ffmpeg(args: list[str]) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-y", *args, "-frames:v", "1", "-c:v", "png", "-f", "image2pipe", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    out, err = await proc.communicate()
    if proc.returncode != 0 or not out:
        raise RuntimeError(f"ffmpeg: {err.decode(errors='replace')[-300:]}")
    return out


async def crop(jpeg: bytes, frac: tuple[float, float, float, float]) -> bytes:
    """The frame cut to the box (clamped to the frame), scaled to at most CARD_WIDTH, as PNG."""
    w, h = jpeg_size(jpeg)
    left, top = min(int(frac[0] * w), w - 1), min(int(frac[1] * h), h - 1)
    cw, ch = max(1, min(round(frac[2] * w), w) - left), max(1, min(round(frac[3] * h), h) - top)
    with tempfile.TemporaryDirectory() as tmp:
        frame = Path(tmp) / "frame.jpg"
        frame.write_bytes(jpeg)
        return await _ffmpeg(
            ["-i", str(frame), "-vf", f"crop={cw}:{ch}:{left}:{top},scale='min({CARD_WIDTH},iw)':-2"]
        )


async def cut_out(client, jpeg: bytes, category: str) -> tuple[str, tuple[int, int]] | None:
    """(PNG data URL, (infer_start_ms, infer_end_ms)), or None when SAM found nothing."""
    res = await client.sam(frames.data_url(jpeg), noun(category), purpose="card_cutout")
    frac = box(res.raw)
    if not frac:
        log.warning("sam reply has no box; reply shape: %s", shape(res.raw))
        return None
    image = await crop(jpeg, frac)
    return frames.data_url(image, "image/png"), (res.infer_start_ms, res.infer_end_ms)
