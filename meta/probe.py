"""One real call per model, to check request/response shapes and that the key works (D-007, D-014).

    python -m meta.probe [--base-url URL] [--session probe-YYYYMMDD] [--only spark,sam,voice]

Needs the app running: spend caps are read from it and every call is posted there as a model_call event.
Inputs are synthetic (an ffmpeg-drawn image, 10 s of macOS `say` speech). The key and headers are never
printed. Raw replies go to data/local/probe/ (never committed), for writing test fixtures by hand.
"""

import argparse
import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import httpx

from app.contracts import make_event, now_ms
from app.settings import ROOT, env
from meta.client import MetaClient, output_text
from meta.meter import Meter, http_spend
from meta.transport import HttpTransport

OUT = ROOT / "data" / "local" / "probe"
SPARK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"shape": {"type": "string"}, "color": {"type": "string"}, "count": {"type": "integer"}},
    "required": ["shape", "color", "count"],
}
SPEECH = (
    "Chào cả nhà. Đôi giày thể thao trắng này size ba mươi chín, giá bốn trăm năm mươi nghìn. "
    "Ai lấy thì bình luận chốt nhé."
)
SAMPLE_RATE, FRAME_BYTES = 24000, 1920  # 80 ms of PCM16 mono at 24 kHz


def test_image() -> str:
    """A grey 640x480 JPEG with one red rectangle, as a data URL."""
    jpeg = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         "color=c=0xd0d0d0:s=640x480,drawbox=x=220:y=160:w=200:h=160:color=red@1:t=fill",
         "-frames:v", "1", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def test_speech(seconds: int = 10) -> bytes:
    """`seconds` of synthetic Vietnamese speech (padded with silence) as PCM16 LE mono 24 kHz."""
    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / "speech.aiff"
        subprocess.run(["say", "-v", "Linh", "-o", str(aiff), SPEECH], check=True)
        return subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(aiff), "-af", "apad", "-t", str(seconds),
             "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"],
            check=True, capture_output=True,
        ).stdout  # fmt: skip


async def paced(pcm: bytes):
    """80 ms frames at real time, as the realtime endpoint expects."""
    t0 = time.monotonic()
    for k, i in enumerate(range(0, len(pcm), FRAME_BYTES)):
        await asyncio.sleep(max(0.0, t0 + k * 0.08 - time.monotonic()))
        yield pcm[i : i + FRAME_BYTES]


def keys(body: dict) -> dict:
    """Field names of a reply, one level into output[], content[] and usage (no values)."""
    out = {"top": sorted(body)}
    items = body.get("output") or []
    out["output"] = [
        {"type": it.get("type"), "content": [c.get("type") for c in it.get("content") or []]} for it in items
    ]
    out["usage"] = {k: sorted(v) if isinstance(v, dict) else v for k, v in (body.get("usage") or {}).items()}
    return out


def line(call: dict) -> str:
    units = f" units={json.dumps(call['units'])}" if call.get("units") else ""
    return (
        f"model_call model={call['model']} purpose={call['purpose']} tokens_in={call['tokens_in']} "
        f"tokens_out={call['tokens_out']} cost_usd={call['cost_usd']:.6f} latency_ms={call['latency_ms']} "
        f"ok={call['ok']} error={call['error']}{units}"
    )


async def spark(client: MetaClient, image: str, reasoning: str) -> dict:
    content = [
        {"type": "input_text", "text": "What shape and colour is the object, and how many are there?"},
        {"type": "input_image", "image_url": image},
    ]
    res = await client.responses(
        [{"type": "message", "role": "user", "content": content}],
        SPARK_SCHEMA,
        purpose="probe_spark",
        name="probe",
        reasoning=reasoning,
    )
    (OUT / "spark.json").write_text(json.dumps(res.raw, ensure_ascii=False, indent=1))
    return {"data": res.data, "shape": keys(res.raw)}


async def sam(client: MetaClient, image: str) -> dict:
    res = await client.sam(image, "red rectangle", purpose="probe_sam")
    (OUT / "sam.json").write_text(json.dumps(res.raw, ensure_ascii=False, indent=1))
    first = res.data[0] if res.data else None
    if first and first["mask"]:
        first = {**first, "mask": {**first["mask"], "payload": f"<{len(first['mask']['payload'])} chars>"}}
    return {
        "objects": len(res.data),
        "first": first,
        "text_chars": len(output_text(res.raw)),
        "shape": keys(res.raw),
    }


async def voice(client: MetaClient, pcm: bytes, mode: str) -> dict:
    config = {"mode": mode, "keywords": ["giày thể thao"], "languageBias": "vi"}
    messages = [m async for m in client.voice_stream(paced(pcm), purpose="probe_voice", config=config)]
    (OUT / "voice.json").write_text(json.dumps(messages, ensure_ascii=False, indent=1))
    finals = [m.get("transcript") for m in messages if m.get("type") == "speechComplete" or m.get("final")]
    return {
        "types": dict(Counter(m.get("type", "<handshake>") for m in messages)),
        "fields": {m.get("type", "<handshake>"): sorted(m) for m in messages},
        "final": finals,
    }


async def run(args) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    calls: list[dict] = []
    http = httpx.AsyncClient(base_url=args.base_url, timeout=10)

    async def emit(fields: dict) -> None:
        calls.append(fields)
        event = make_event("model_call", args.session, now_ms(), "sim", **fields)
        (await http.post(f"/api/sessions/{args.session}/events", json=[event])).raise_for_status()

    client = MetaClient(HttpTransport(), Meter(http_spend(args.base_url)), emit)
    image = test_image()
    steps = {
        "spark": lambda: spark(client, image, args.reasoning),
        "sam": lambda: sam(client, image),
        "voice": lambda: voice(client, test_speech(), args.voice_mode),
    }
    failed = 0
    for name in args.only.split(","):
        before = len(calls)
        try:
            result = await steps[name]()
        except Exception as exc:  # report and go on to the next model
            failed += 1
            result = {"failed": f"{type(exc).__name__}: {str(exc)[:400]}"}
        for call in calls[before:]:
            print(line(call))
        print(f"  {name}: {json.dumps(result, ensure_ascii=False)}")
    await http.aclose()
    cost = await http_spend(args.base_url)()
    print(f"spend after probe: today_usd={cost[0]:.6f} total_usd={cost[1]:.6f} (session {args.session})")
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default=env("BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--session", default=time.strftime("probe-%Y%m%d"))
    ap.add_argument("--only", default="spark,sam,voice")
    ap.add_argument("--reasoning", default="minimal", help="Spark reasoning.effort for the probe call")
    ap.add_argument("--voice-mode", default="ENDPOINTING")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
