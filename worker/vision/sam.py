"""SAM 3.1 card image: one short English noun phrase per call, the frame as a data URL.

Reply verified on 2026-10-27 (D-020): output text with tokens
`<|box;x1=..;y1=..;x2=..;y2=..;w=<frameW>;h=<frameH>|><|mask;x=0;y=0;data=<H>,<W>,<marker><base85>|>`, which
meta.client.parse_segmentation reads into `res.data`. The box is in source pixels, corners inclusive. The mask is
box-local (x=y=0, H x W = the box size); `meta_sam_parser.decode_mask_to_raster` turns it into 0/1 bytes, row-major
with the top row first. The card image is the frame cut to SAM's first box with that mask as its alpha channel:
real pixels, nothing retouched or generated, a PNG data URL at most 360 px wide. A mask that is missing, not
box-sized or does not decode leaves the plain box crop (logged); when SAM finds nothing the card keeps image_url null.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

from meta_sam_parser import InvalidSegmentationMaskError, SegmentationMask, decode_mask_to_raster

from worker.media.extract import jpeg_size
from worker.vision import frames
from worker.voice import vn

log = logging.getLogger("worker.sam")
CARD_WIDTH = 360
SCALE = f"scale='min({CARD_WIDTH},iw)':-1"  # never upscaled; at or under 360 px wide the pixels pass through as-is
MARKERS = {
    "lossless": "~",
    "one_bit": "!",
}  # parse_segmentation drops the payload's marker; the decoder needs it
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

# An 'other' item's catalog kind -> the English noun SAM is asked for; asked for "other", SAM often finds
# nothing (D-039).
KINDS = {" ".join(vn.tokens(k)): v for k, v in {"tay cầm": "game controller", "bàn phím": "keyboard"}.items()}


def subject(category: str, kind: str | None = None) -> str:
    """What a card asks SAM to find, as cut_out's category: an 'other' item's kind when the table names it (the
    kind normalised as vn.tokens reads it), otherwise the category itself."""
    if category == "other" and isinstance(kind, str):
        return KINDS.get(" ".join(vn.tokens(kind)), category)
    return category


def noun(category: str, kind: str | None = None) -> str:
    c = subject(category, kind)
    return NOUNS.get(c, c.replace("_", " "))


def has_box(rec: dict) -> bool:
    b = rec.get("box") or {}
    return (
        all(isinstance(b.get(k), int) for k in ("x1", "y1", "x2", "y2", "w", "h"))
        and b["w"] > 0
        and b["h"] > 0
    )


def frac(b: dict) -> tuple[float, float, float, float]:
    """A record's box as fractions of the frame (left, top, right, bottom)."""
    return b["x1"] / b["w"], b["y1"] / b["h"], (b["x2"] + 1) / b["w"], (b["y2"] + 1) / b["h"]


def alpha(rec: dict) -> tuple[int, int, bytes] | None:
    """(width, height, 8-bit alpha 0/255, row-major) from the record's mask, or None when it cannot be used."""
    b, m = rec["box"], rec.get("mask")
    w, h = b["x2"] - b["x1"] + 1, b["y2"] - b["y1"] + 1
    if (
        not m
        or m.get("encoding") not in MARKERS
        or (m.get("x"), m.get("y")) != (0, 0)
        or (m.get("w"), m.get("h")) != (w, h)
    ):
        log.warning(
            "sam mask not usable (box %dx%d): %s",
            w,
            h,
            {k: v for k, v in (m or {}).items() if k != "payload"},
        )
        return None
    mask = SegmentationMask(
        encoding=m["encoding"], payload=MARKERS[m["encoding"]] + m["payload"], width=w, height=h
    )
    try:
        raster = decode_mask_to_raster(mask)
    except InvalidSegmentationMaskError as exc:
        log.warning("sam mask does not decode: %s", exc)
        return None
    return w, h, raster.replace(b"\x01", b"\xff")


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


async def crop(
    jpeg: bytes, frac: tuple[float, float, float, float], mask: tuple[int, int, bytes] | None = None
) -> bytes:
    """The frame cut to the box (clamped to the frame), with the mask as alpha when given, as PNG."""
    w, h = jpeg_size(jpeg)
    left, top = min(int(frac[0] * w), w - 1), min(int(frac[1] * h), h - 1)
    cw, ch = max(1, min(round(frac[2] * w), w) - left), max(1, min(round(frac[3] * h), h) - top)
    with tempfile.TemporaryDirectory() as tmp:
        frame = Path(tmp) / "frame.jpg"
        frame.write_bytes(jpeg)
        if mask is None:
            return await _ffmpeg(
                ["-i", str(frame), "-vf", f"format=rgb24,crop={cw}:{ch}:{left}:{top},{SCALE}"]
            )
        mw, mh, data = mask
        pgm = Path(tmp) / "alpha.pgm"
        pgm.write_bytes(b"P5\n%d %d\n255\n" % (mw, mh) + data)
        graph = (
            f"[0]format=rgba,crop={cw}:{ch}:{left}:{top}[c];[1]scale={cw}:{ch}:flags=neighbor,format=gray[a];"
            f"[c][a]alphamerge,{SCALE}"
        )  # RGB(A) before the crop in both paths: on the JPEG's 4:2:0 an odd width or left edge is rounded to even
        # and the mask no longer fits. The mask is box-sized; it is only rescaled if SAM's frame size differs
        return await _ffmpeg(["-i", str(frame), "-i", str(pgm), "-filter_complex", graph, "-pix_fmt", "rgba"])


async def cut_out(client, jpeg: bytes, category: str) -> tuple[str, tuple[int, int]] | None:
    """(PNG data URL, (infer_start_ms, infer_end_ms)), or None when SAM found nothing."""
    res = await client.sam(frames.data_url(jpeg), noun(category), purpose="card_cutout")
    records = [r for r in res.data or [] if has_box(r)]
    if not records:
        log.warning("sam found no %s; reply shape: %s", noun(category), shape(res.raw))
        return None
    rec = records[0]
    image = await crop(jpeg, frac(rec["box"]), await asyncio.to_thread(alpha, rec))
    return frames.data_url(image, "image/png"), (res.infer_start_ms, res.infer_end_ms)
