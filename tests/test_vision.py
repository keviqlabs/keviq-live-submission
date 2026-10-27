"""worker/vision: Spark frame -> product_seen only on a change of product; SAM box + mask -> card cut-out (D-020)."""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from meta_sam_parser._mask_codec import _encode_raster

from app.contracts import make_event, now_ms
from meta import probe
from meta.client import MetaClient, parse_segmentation
from meta.meter import Meter, memory_spend
from meta.transport import FakeTransport, Reply, responses_reply
from worker.runner import Runner
from worker.vision import frames, sam, spark

SID = "vision1"
JPEG = b"\xff\xd8not-a-real-jpeg"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def fresh():
    spark.LAST.clear()
    spark._WARNED.clear()
    frames._CACHE.clear()


def frame(i: int, ts: int) -> dict:
    return make_event(
        "frame", SID, ts, "replay", event_id=f"fr-{i}", jpeg_b64=base64.b64encode(JPEG).decode(), width=640,
        height=360,
    )  # fmt: skip


def answer(
    category: str, color: str | None = None, confidence: float = 0.9, has_product: bool = True
) -> Reply:
    body = {"has_product": has_product, "category": category, "color": color, "size": None, "material": None,
            "confidence": confidence}  # fmt: skip
    return responses_reply(json.dumps(body))


async def run_frames(mem_app, replies: list[Reply]) -> tuple[list[list[dict]], FakeTransport]:
    _, _, client = mem_app
    runner = Runner("http://test", {"frame": [spark.see_product]}, http=client)
    transport = FakeTransport(replies)
    runner._meta = (transport, Meter(memory_spend([])))
    t0, outs = now_ms(), []
    for i in range(len(replies)):
        event = frame(i, t0 + i * 1000)
        await client.post(f"/api/sessions/{SID}/events", json=[event])
        outs.append(await runner.handle(event))
    return outs, transport


async def test_only_a_change_of_product_is_emitted(mem_app):
    replies = [
        answer("none", has_product=False),
        answer("sneaker", "Trắng "),
        answer("sneaker", "trắng"),
        answer("sneaker", None),
        answer("sneaker", "đen"),
        answer("bag", "đỏ", confidence=0.3),
        answer("bag", "đỏ"),
    ]
    outs, transport = await run_frames(mem_app, replies)
    emitted = [(o[0]["category"], o[0]["attributes"]["color"]) if o else None for o in outs]
    assert emitted == [None, ("sneaker", "trắng"), None, None, ("sneaker", "đen"), None, ("bag", "đỏ")]
    first = outs[1][0]
    assert first["is_new"] and first["caused_by"] == ["fr-1"] and first["frame_ts_ms"] == first["ts_ms"]
    assert set(first["timing"]) == {"received_ms", "infer_start_ms", "infer_end_ms", "emitted_ms"}
    body = transport.requests[2][1]
    assert body["text"]["format"]["strict"] is True
    assert body["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,")
    assert "sneaker màu trắng" in body["input"][0]["content"][0]["text"]  # the last product is in the prompt


async def test_model_failure_skips_the_frame_and_keeps_the_frame_cached(mem_app):
    outs, _ = await run_frames(mem_app, [Reply(400, {"error": "bad request"}), Reply(400, {})])
    assert outs == [[], []]
    assert frames.get(SID, max(frames._CACHE[SID])) == JPEG


# The verified reply shape (D-020) with made-up numbers and a mask payload that does not decode.
SAM_TEXT = "<0f>0<|box;x1=240;y1=100;x2=399;y2=259;w=640;h=360|><|mask;x=0;y=0;data=160,160,!!!!#0CZI|>"
# The real SAM 3.1 output text for meta.probe.test_image(): a 200x160 red box at (220, 160) on a grey 640x480 frame.
REAL_TEXT = (Path(__file__).parent / "fixtures" / "sam_red_box.txt").read_text()


def test_box_read_from_the_record():
    rec = parse_segmentation(SAM_TEXT)[0]
    assert sam.has_box(rec) and sam.frac(rec["box"]) == (240 / 640, 100 / 360, 400 / 640, 260 / 360)
    assert not sam.has_box({"box": {"x1": 1}}) and not sam.has_box({})
    assert sam.noun("heels") == "high heel shoe" and sam.noun("new_thing") == "new thing"


def test_real_mask_decodes_to_the_box_size_and_covers_the_red_box():
    w, h, a = sam.alpha(parse_segmentation(REAL_TEXT)[0])
    assert (w, h) == (200, 160) and len(a) == 200 * 160 and set(a) == {0, 255}
    assert a.count(255) / len(a) >= 0.95


@pytest.mark.parametrize("field,value", [("w", 199), ("x", 1), ("encoding", None)])
def test_mask_that_does_not_fit_the_box_is_not_used(field, value):
    rec = parse_segmentation(REAL_TEXT)[0]
    rec["mask"][field] = value
    assert sam.alpha(rec) is None


def _lavfi(source: str, *out: str) -> bytes:
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source, "-frames:v", "1", *out, "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def _rgba(png: bytes) -> tuple[int, int, bytes]:
    w, h = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    cmd = ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"]
    return w, h, subprocess.run(cmd, input=png, capture_output=True, check=True).stdout


async def _cut_out(text: str, jpeg: bytes) -> bytes:
    client = MetaClient(FakeTransport([responses_reply(text)]), Meter(memory_spend([])), emit=_ignore)
    url, (start, end) = await sam.cut_out(client, jpeg, "sneaker")
    assert url.startswith("data:image/png;base64,") and end >= start
    return base64.b64decode(url.split(",", 1)[1])


@needs_ffmpeg
async def test_real_mask_becomes_the_alpha_of_the_box_crop():
    jpeg = base64.b64decode(probe.test_image().split(",", 1)[1])
    png = await _cut_out(REAL_TEXT, jpeg)
    w, h, raw = _rgba(png)
    a = raw[3::4]
    assert png[25] == 6 and (w, h) == (200, 160)  # PNG colour type 6 = RGBA
    assert a == sam.alpha(parse_segmentation(REAL_TEXT)[0])[2] and a.count(255) / len(a) >= 0.95
    _, _, plain = _rgba(await sam.crop(jpeg, sam.frac(parse_segmentation(REAL_TEXT)[0]["box"])))
    assert all(raw[i : i + 3] == plain[i : i + 3] for i in range(0, len(raw), 4))  # pixels untouched


@needs_ffmpeg
async def test_mask_rows_run_top_down_and_columns_left_to_right():
    w, h = 40, 20
    quarter = bytes(1 if x < w // 2 and y >= h // 2 else 0 for y in range(h) for x in range(w))  # bottom-left
    payload = _encode_raster(quarter, w, h)
    text = f"<0f>0<|box;x1=10;y1=20;x2={9 + w};y2={19 + h};w=64;h=48|><|mask;x=0;y=0;data={h},{w},{payload}|>"
    _, _, raw = _rgba(await _cut_out(text, _lavfi("color=c=gray:s=64x48", "-f", "mjpeg")))
    assert raw[3::4] == bytes(255 * v for v in quarter)


@needs_ffmpeg
async def test_box_with_odd_width_and_left_edge_keeps_its_mask():
    w, h = 41, 21  # a 4:2:0 JPEG crops to even sizes unless the frame is RGBA first
    left = bytes(1 if x < 20 else 0 for y in range(h) for x in range(w))
    payload = _encode_raster(left, w, h)
    text = (
        f"<0f>0<|box;x1=11;y1=21;x2={10 + w};y2={20 + h};w=64;h=48|><|mask;x=0;y=0;data={h},{w},{payload}|>"
    )
    jpeg = _lavfi("color=c=gray:s=64x48", "-f", "mjpeg")
    rw, rh, raw = _rgba(await _cut_out(text, jpeg))
    assert (rw, rh) == (w, h) and raw[3::4] == bytes(255 * v for v in left)
    plain = await sam.crop(jpeg, sam.frac(parse_segmentation(text)[0]["box"]))
    assert (int.from_bytes(plain[16:20], "big"), int.from_bytes(plain[20:24], "big")) == (w, h)


@needs_ffmpeg
async def test_mask_that_does_not_decode_keeps_the_box_crop():
    png = await _cut_out(SAM_TEXT, _lavfi("testsrc=size=640x360", "-f", "mjpeg"))
    assert png[25] == 2  # RGB, background kept
    assert (int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")) == (160, 160)


async def test_reply_without_a_box_leaves_the_card_without_one():
    transport = FakeTransport([responses_reply("<0f>0")])
    client = MetaClient(transport, Meter(memory_spend([])), emit=_ignore)
    assert await sam.cut_out(client, JPEG, "bag") is None
    assert transport.requests[0][1]["input"][0]["content"][0]["text"] == "handbag"


async def _ignore(fields: dict) -> None:
    pass
