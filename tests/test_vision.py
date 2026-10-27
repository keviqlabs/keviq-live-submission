"""worker/vision: Spark frame -> product_seen only on a change of product; SAM box -> crop of the frame (D-020)."""

import base64
import json
import shutil
import subprocess

import pytest

from app.contracts import make_event, now_ms
from meta.client import MetaClient
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


# The verified reply shape (D-020) with made-up numbers and a dummy mask payload.
SAM_TEXT = "<0f>0<|box;x1=240;y1=100;x2=399;y2=259;w=640;h=360|><|mask;x=0;y=0;data=160,160,!!!!#0CZI|>"


def test_box_read_from_the_token_text():
    assert sam.box(responses_reply(SAM_TEXT).body) == (240 / 640, 100 / 360, 400 / 640, 260 / 360)
    assert sam.box(responses_reply("<0f>0").body) is None
    assert sam.noun("heels") == "high heel shoe" and sam.noun("new_thing") == "new thing"


def _lavfi(source: str, *out: str) -> bytes:
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source, "-frames:v", "1", *out, "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


@needs_ffmpeg
async def test_box_reply_becomes_a_crop_of_the_frame():
    jpeg = _lavfi("testsrc=size=640x360", "-f", "mjpeg")
    transport = FakeTransport([responses_reply(SAM_TEXT)])
    client = MetaClient(transport, Meter(memory_spend([])), emit=_ignore)
    url, (start, end) = await sam.cut_out(client, jpeg, "sneaker")
    png = base64.b64decode(url.split(",", 1)[1])
    assert url.startswith("data:image/png;base64,") and png.startswith(b"\x89PNG")
    assert (int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")) == (160, 160)
    assert end >= start and transport.requests[0][1]["input"][0]["content"][0]["text"] == "sneaker"


async def test_reply_without_a_box_leaves_the_card_without_one():
    transport = FakeTransport([responses_reply("<0f>0")])
    client = MetaClient(transport, Meter(memory_spend([])), emit=_ignore)
    assert await sam.cut_out(client, JPEG, "bag") is None


async def _ignore(fields: dict) -> None:
    pass
