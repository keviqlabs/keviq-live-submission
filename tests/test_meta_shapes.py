"""meta/: request and reply shapes as seen on the first real call per model (D-014), replayed offline."""

import asyncio
import json

import pytest

from meta.client import parse_segmentation
from meta.transport import Reply
from tests.test_meta import SCHEMA, make

# Shortened from the real SAM 3.1 reply: a 200x160 red box drawn at (220, 160) on a 640x480 image.
SAM_TEXT = (
    "<0f>0<|box;x1=220;y1=160;x2=419;y2=319;w=640;h=480;c=0.6167|>"
    "<|mask;x=0;y=0;c=0.6167;data=160,200,~!!!kP#U{29j|>"
)


def reply(output, **usage):
    return Reply(200, {"id": "resp_1", "object": "response", "status": "completed", "output": output,
                       "usage": {"input_tokens": 0, "output_tokens": 0, **usage}}, {})  # fmt: skip


def message(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def test_parse_segmentation_reads_box_mask_and_frames():
    rec = parse_segmentation(SAM_TEXT)
    assert rec == [
        {
            "frame": 0,
            "id": 0,
            "box": {"x1": 220, "y1": 160, "x2": 419, "y2": 319, "w": 640, "h": 480, "c": 0.6167},
            "mask": {"x": 0, "y": 0, "c": 0.6167, "h": 160, "w": 200, "encoding": "lossless",
                     "payload": "!!!kP#U{29j"},
        }
    ]  # fmt: skip
    two = parse_segmentation(
        "<0f>0<|box;x1=1;y1=2;x2=3;y2=4;w=9;h=9|>,1<|box;x1=5;y1=6;x2=7;y2=8;w=9;h=9|>\n<1f>"
    )
    assert [(r["frame"], r["id"], r["mask"]) for r in two] == [(0, 0, None), (0, 1, None)]
    assert parse_segmentation("") == []


async def test_sam_request_shape_and_parsed_reply():
    client, transport, calls, _ = make([reply([message(SAM_TEXT)], images_processed=1)])
    out = await client.sam("data:image/jpeg;base64,AAAA", "red rectangle")
    path, body = transport.requests[0]
    assert path == "/responses" and body["stream"] is False
    assert body["metadata"] == {"include_confidence": "true", "mask_encoding": "lossless"}
    assert body["input"] == [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "red rectangle"},
        {"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"}]}]  # fmt: skip
    assert out.data[0]["box"]["x2"] == 419 and out.data[0]["mask"]["encoding"] == "lossless"
    assert calls[0]["cost_usd"] == pytest.approx(0.0025)


async def test_spark_reply_with_reasoning_item_and_effort_param():
    output = [{"type": "reasoning", "content": []}, message(json.dumps({"label": "order"}))]
    usage = {"input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 90}}
    client, transport, calls, _ = make(
        [reply(output, **{**usage, "input_tokens": 415, "output_tokens": 111})]
    )
    out = await client.responses(input="x", schema=SCHEMA, purpose="p", reasoning="minimal")
    assert out.data == {"label": "order"}
    assert transport.requests[0][1]["reasoning"] == {"effort": "minimal"}
    assert (calls[0]["tokens_in"], calls[0]["tokens_out"]) == (415, 111)
    assert calls[0]["cost_usd"] == pytest.approx((415 * 1.25 + 111 * 4.25) / 1e6)


class FakeSocket:
    """Plays the real ENDPOINTING sequence; the last partial and close come only after endStream."""

    def __init__(self, before, after):
        self.before, self.after, self.sent, self.ended = before, after, [], asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, frame):
        self.sent.append(frame)
        if isinstance(frame, str) and json.loads(frame).get("type") == "endStream":
            self.ended.set()

    async def __aiter__(self):
        for msg in self.before:
            yield json.dumps(msg)
        await asyncio.wait_for(self.ended.wait(), 2)  # a client that never sends endStream fails, not hangs
        for msg in self.after:
            yield json.dumps(msg)


REAL_SEQUENCE = [
    {"sessionId": "s-1"},
    {"type": "speechStart", "audioProcessedMs": 0, "turnId": 0},
    {"type": "transcript", "transcript": "Chào", "final": False, "audioProcessedMs": 800},
    {"type": "speechEnd", "audioProcessedMs": 8140, "turnId": 0},
    {"type": "speechComplete", "audioProcessedMs": 8140, "turnId": 0, "transcript": "Chào cả nhà"},
]
END = [{"type": "transcript", "transcript": "", "final": True, "audioProcessedMs": 10000}]


def fake_ws(monkeypatch, before, after):
    sock = FakeSocket(before, after)
    urls = []

    def connect(url, **kwargs):
        urls.append((url, kwargs))
        return sock

    monkeypatch.setattr("websockets.connect", connect)
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    return sock, urls


async def chunks(n, size=1920):
    for _ in range(n):
        yield b"\0" * size


async def test_voice_handshake_audio_endstream_and_cost(monkeypatch):
    sock, urls = fake_ws(monkeypatch, REAL_SEQUENCE, END)
    client, _, calls, _ = make([])
    config = {"mode": "ENDPOINTING", "languageBias": "vi", "keywords": ["giày thể thao"]}
    got = [m async for m in client.voice_stream(chunks(125), config=config)]
    assert [m.get("type") for m in got] == [None, "speechStart", "transcript", "speechEnd", "speechComplete",
                                            "transcript"]  # fmt: skip
    assert urls[0][1] == {}  # the key goes in the handshake, not in a header
    handshake = json.loads(sock.sent[0])
    assert handshake["authorization"] == {"accessToken": "Bearer test-key"}
    assert handshake["audioEncoding"] == "PCM_24KHZ" and handshake["mode"] == "ENDPOINTING"
    assert handshake["languageBias"] == ["Vietnamese"] and handshake["keywords"] == ["giày thể thao"]
    assert all(isinstance(f, bytes) for f in sock.sent[1:-1]) and len(sock.sent) == 127
    assert json.loads(sock.sent[-1]) == {"type": "endStream"}
    assert len(calls) == 1 and calls[0]["units"] == {"audio_seconds": 10} and calls[0]["ok"]
    assert calls[0]["cost_usd"] == pytest.approx(10 * 0.18 / 3600)


async def test_voice_error_is_logged_and_processed_audio_still_billed(monkeypatch):
    error = {"type": "error", "message": "bad audio", "sessionId": "s-1", "errorCode": "invalid_audio"}
    fake_ws(monkeypatch, REAL_SEQUENCE[:3] + [error], [])
    client, _, calls, _ = make([])
    got = [m async for m in client.voice_stream(chunks(75))]
    assert got[-1]["type"] == "error"
    assert calls[0]["ok"] is False and calls[0]["error"] == "invalid_audio"
    assert calls[0]["units"] == {"audio_seconds": 3}  # no audioProcessedMs >= 1 s: falls back to bytes sent
    assert calls[0]["cost_usd"] > 0


async def test_voice_rejects_unsupported_sample_rate():
    client, _, calls, _ = make([])
    with pytest.raises(ValueError):
        async for _ in client.voice_stream(chunks(1), sample_rate=44100):
            pass
    assert calls == []
