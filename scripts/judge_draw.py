"""Draws the four demo1 products as flat pictures, in pure Python, so the judge data needs no outside image.

    uv run python scripts/judge_draw.py OUT_DIR      # writes <sku>.png (product, transparent) and <sku>-frame.png

The pictures are made here from polygons and ellipses; no photo, font or image file from elsewhere is used.
`product_png` is the card picture (transparent background); `frame_png` puts it on a wall and a table, the size of
a phone video frame (720 x 1280). Same input, same bytes: nothing random, no timestamp in the PNG.
"""

import math
import struct
import sys
import zlib
from pathlib import Path

SKUS = ("GIAY-01", "TUI-03", "BOOT-02", "BOOT-05")
SS = 3  # samples per pixel along each axis


class Canvas:
    """RGBA picture drawn at SS x SS samples a pixel and averaged down on output (smooth edges)."""

    def __init__(self, w: int, h: int, bg: tuple[int, int, int, int] = (0, 0, 0, 0), k: float = 1.0):
        self.w, self.h, self.k = w * SS, h * SS, k * SS  # k: drawing units to output pixels
        self.px = bytearray(bytes(bg) * (self.w * self.h))

    def poly(self, pts: list[tuple[float, float]], rgba: tuple[int, int, int, int]) -> None:
        """Fill a polygon given in drawing units (even-odd rule)."""
        pts = [(x * self.k, y * self.k) for x, y in pts]
        ys = [p[1] for p in pts]
        edges = list(zip(pts, pts[1:] + pts[:1], strict=True))
        for y in range(max(0, int(min(ys))), min(self.h, int(max(ys)) + 1)):
            yc = y + 0.5
            xs = sorted(
                x0 + (yc - y0) * (x1 - x0) / (y1 - y0)
                for (x0, y0), (x1, y1) in edges
                if (y0 <= yc < y1) or (y1 <= yc < y0)
            )
            for a, b in zip(xs[::2], xs[1::2], strict=False):
                lo, hi = max(0, math.ceil(a - 0.5)), min(self.w, math.floor(b - 0.5) + 1)
                if hi > lo:
                    row = y * self.w
                    self.px[(row + lo) * 4 : (row + hi) * 4] = bytes(rgba) * (hi - lo)

    def ellipse(
        self, cx: float, cy: float, rx: float, ry: float, rgba, a0: float = 0, a1: float = 360
    ) -> None:
        n = 96
        pts = [
            (
                cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n)),
            )
            for i in range(n + 1)
        ]
        self.poly(pts if a1 - a0 >= 360 else [(cx, cy), *pts], rgba)

    def rrect(self, x: float, y: float, w: float, h: float, r: float, rgba) -> None:
        pts = []
        for cx, cy, a in (
            (x + w - r, y + r, -90),
            (x + w - r, y + h - r, 0),
            (x + r, y + h - r, 90),
            (x + r, y + r, 180),
        ):
            pts += [
                (
                    cx + r * math.cos(math.radians(a + 90 * i / 12)),
                    cy + r * math.sin(math.radians(a + 90 * i / 12)),
                )
                for i in range(13)
            ]
        self.poly(pts, rgba)

    def line(self, pts: list[tuple[float, float]], width: float, rgba) -> None:
        """A polyline as one quad per segment plus round joints."""
        for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
            d = math.hypot(x1 - x0, y1 - y0) or 1
            nx, ny = -(y1 - y0) / d * width / 2, (x1 - x0) / d * width / 2
            self.poly([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)], rgba)
        for x, y in pts:
            self.ellipse(x, y, width / 2, width / 2, rgba)

    def paste(self, other: "Canvas", ox: int, oy: int) -> None:
        """Alpha-blend another canvas (same sample scale) at pixel offset ox, oy."""
        src, dst = other.px, self.px
        for y in range(other.h):
            ty = y + oy * SS
            if not 0 <= ty < self.h:
                continue
            for x in range(other.w):
                tx = x + ox * SS
                i = (y * other.w + x) * 4
                a = src[i + 3]
                if a == 0 or not 0 <= tx < self.w:
                    continue
                j = (ty * self.w + tx) * 4
                for k in range(3):
                    dst[j + k] = (src[i + k] * a + dst[j + k] * (255 - a)) // 255
                dst[j + 3] = max(dst[j + 3], a)

    def png(self) -> bytes:
        w, h, n = self.w // SS, self.h // SS, SS * SS
        raw = bytearray()
        for y in range(h):
            raw.append(0)
            for x in range(w):
                acc = [0, 0, 0, 0]
                for dy in range(SS):
                    base = ((y * SS + dy) * self.w + x * SS) * 4
                    for dx in range(SS):
                        i = base + dx * 4
                        a = self.px[i + 3]
                        acc[0] += self.px[i] * a
                        acc[1] += self.px[i + 1] * a
                        acc[2] += self.px[i + 2] * a
                        acc[3] += a
                a = acc[3]
                raw += bytes((acc[0] // a, acc[1] // a, acc[2] // a, a // n) if a else (0, 0, 0, 0))

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        head = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", head)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
        )


INK = (60, 52, 48, 255)


def sneaker(c: Canvas) -> None:
    """White low sneaker, side view, toe to the right, white rubber sole."""
    upper = [
        (70, 250),
        (78, 175),
        (110, 140),
        (150, 150),
        (175, 185),
        (240, 200),
        (300, 215),
        (335, 235),
        (345, 262),
        (70, 262),
    ]
    c.poly([(x + (3 if x > 200 else -3), y + (-3 if y < 255 else 3)) for x, y in upper], INK)
    c.poly(upper, (246, 246, 244, 255))
    c.rrect(58, 255, 300, 38, 16, INK)
    c.rrect(61, 258, 294, 32, 14, (236, 234, 228, 255))
    c.line([(64, 276), (352, 276)], 3, (190, 186, 178, 255))
    c.poly([(150, 150), (175, 185), (165, 190), (140, 158)], (225, 225, 222, 255))
    for i in range(5):
        x = 172 + i * 18
        c.line([(x, 188 + i * 4), (x + 14, 202 + i * 3)], 4, INK)
    c.ellipse(98, 178, 12, 20, (210, 210, 206, 255))


def bag(c: Canvas) -> None:
    """Red leather cross-body bag with a long strap and a gold clasp."""
    c.line([(110, 190), (120, 60), (200, 30), (280, 60), (290, 190)], 9, (120, 22, 28, 255))
    c.rrect(80, 170, 240, 170, 26, (110, 18, 24, 255))
    c.rrect(84, 174, 232, 162, 23, (196, 32, 42, 255))
    c.poly([(84, 196), (316, 196), (316, 262), (200, 288), (84, 262)], (150, 22, 32, 255))
    c.rrect(184, 268, 32, 30, 6, (214, 170, 60, 255))
    c.rrect(192, 276, 16, 14, 3, (150, 110, 30, 255))
    c.ellipse(110, 190, 10, 10, (214, 170, 60, 255))
    c.ellipse(290, 190, 10, 10, (214, 170, 60, 255))


def boot(c: Canvas, leather: tuple[int, int, int, int], dark: tuple[int, int, int, int]) -> None:
    """Short leather ankle boot, side view, toe to the right, block heel."""
    shape = [(110, 70), (215, 70), (222, 200), (300, 220), (338, 245), (345, 280), (110, 280)]
    c.poly([(x + (4 if x > 160 else -4), y + (-4 if y < 200 else 4)) for x, y in shape], (30, 24, 20, 255))
    c.poly(shape, leather)
    c.rrect(104, 278, 246, 22, 8, (35, 30, 28, 255))
    c.rrect(108, 292, 60, 34, 4, (35, 30, 28, 255))
    c.poly([(110, 70), (215, 70), (217, 92), (110, 92)], dark)
    c.line([(205, 100), (210, 200)], 4, dark)
    c.ellipse(175, 82, 8, 6, (160, 160, 160, 255))


def product(sku: str, k: float = 1.0) -> Canvas:
    """The product drawn on a 400 x 400 unit square, k output pixels per unit."""
    c = Canvas(round(400 * k), round(400 * k), k=k)
    if sku == "GIAY-01":
        sneaker(c)
    elif sku == "TUI-03":
        bag(c)
    elif sku == "BOOT-02":
        boot(c, (40, 36, 34, 255), (20, 18, 17, 255))
    elif sku == "BOOT-05":
        boot(c, (128, 78, 42, 255), (92, 54, 28, 255))
    else:
        raise ValueError(sku)
    return c


def product_png(sku: str) -> bytes:
    return product(sku).png()


def frame_png(sku: str, w: int = 720, h: int = 1280) -> bytes:
    """The product on a table in front of a plain wall, at phone-video size."""
    c = Canvas(w, h, (226, 214, 196, 255))
    c.poly([(0, 760), (w, 760), (w, h), (0, h)], (150, 104, 66, 255))
    c.poly([(0, 760), (w, 760), (w, 776), (0, 776)], (120, 80, 50, 255))
    k = 1.4
    c.paste(product(sku, k), round((w - 400 * k) / 2), round(780 - 330 * k))
    return c.png()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit(__doc__.strip().splitlines()[2])
    out = Path(args[0])
    out.mkdir(parents=True, exist_ok=True)
    for sku in SKUS:
        (out / f"{sku}.png").write_bytes(product_png(sku))
        (out / f"{sku}-frame.png").write_bytes(frame_png(sku))
        print(f"{out / sku}.png, {out / sku}-frame.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
