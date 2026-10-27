"""Thin Model API client: every call is cap-checked first and logged as a model_call afterwards.

    client = MetaClient(transport, meter, emit)
    out = await client.responses(input=..., schema={...}, purpose="label_comments")

`emit(fields)` receives the model_call fields (model, purpose, tokens, cost, latency, ok, error, units);
the caller wraps them into an event for its session. Request and response shapes follow dev.meta.ai docs and
were checked with one real call per model (`python -m meta.probe`, docs/DECISIONS.md D-014).
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from jsonschema import Draft202012Validator

from app.settings import env
from meta.meter import BudgetExceeded, Meter
from meta.pricing import BLOCKED, cost_usd

log = logging.getLogger("meta.client")
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
VOICE_ENCODINGS = {16000: "PCM_16KHZ", 24000: "PCM_24KHZ"}
LANGUAGE_NAMES = {"vi": "Vietnamese", "en": "English"}  # languageBias takes language names, not codes


class SchemaError(ValueError):
    """The model output did not match the schema twice; the caller marks the item `unresolved`."""


class ModelError(RuntimeError):
    pass


@dataclass
class Result:
    data: object
    raw: dict
    cost_usd: float
    latency_ms: int
    infer_start_ms: int
    infer_end_ms: int


def _ms() -> int:
    return time.time_ns() // 1_000_000


def output_text(body: dict) -> str:
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    parts = []
    for item in body.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in ("output_text", "text") and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "".join(parts)


def usage(body: dict) -> tuple[int, int, int]:
    u = body.get("usage") or {}
    tokens_in = u.get("input_tokens", u.get("prompt_tokens", 0)) or 0
    tokens_out = u.get("output_tokens", u.get("completion_tokens", 0)) or 0
    cached = (u.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
    return tokens_in, tokens_out, cached


FRAME = re.compile(r"<(\d+)f>")
RECORD = re.compile(r"(\d+)<\|box;([^|]*)\|>(?:<\|mask;(.*?)\|>)?", re.S)
MASK_ENCODINGS = {"~": "lossless", "!": "one_bit"}


def _fields(text: str) -> dict:
    out = {}
    for pair in filter(None, text.split(";")):
        key, _, value = pair.partition("=")
        try:
            out[key] = float(value) if "." in value else int(value)
        except ValueError:
            out[key] = value
    return out


def parse_segmentation(text: str) -> list[dict]:
    """SAM 3.1 output text -> [{frame, id, box, mask}].

    One line per frame: `<Nf>` then `id<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..[;c=..]|><|mask;x=0;y=0[;c=..];
    data=H,W,<enc>payload|>` records joined by commas. Seen on the real reply: x1..y2 are inclusive pixel
    corners, box w/h are the source image size (not the box size), and the mask is H x W = the box size. The
    mask payload (base85, `~` lossless, `!` one_bit) is kept as text; pixels are decoded by the caller, e.g.
    `meta_sam_parser.decode_mask_to_raster`. No match is an empty text, not an error.
    """
    records = []
    for line in text.splitlines():
        marker = FRAME.search(line)
        if not marker:
            continue
        for rec in RECORD.finditer(line, marker.end()):
            mask = None
            if rec.group(3) is not None:
                head, _, data = rec.group(3).partition("data=")
                height, width, payload = (data.split(",", 2) + ["", "", ""])[:3]
                mask = {
                    **_fields(head),
                    "h": int(height) if height.isdigit() else None,
                    "w": int(width) if width.isdigit() else None,
                    "encoding": MASK_ENCODINGS.get(payload[:1]),
                    "payload": payload[1:],
                }
            records.append(
                {
                    "frame": int(marker.group(1)),
                    "id": int(rec.group(1)),
                    "box": _fields(rec.group(2)),
                    "mask": mask,
                }
            )
    return records


class MetaClient:
    def __init__(
        self,
        transport,
        meter: Meter,
        emit: Callable[[dict], Awaitable[None]],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        session_id: str | None = None,
    ):
        """`session_id` is the session the calls are made for; the meter checks its per-session cap."""
        self.transport, self.meter, self.emit, self.sleep = transport, meter, emit, sleep
        self.session_id = session_id
        self.models = {
            "spark": env("MODEL_SPARK", "muse-spark-1.3"),
            "sam": env("MODEL_SAM", "sam-3.1"),
            "voice": env("MODEL_VOICE", "muse-voice-transcribe-1.0"),
            "image": env("MODEL_IMAGE", "muse-image-1.0"),
        }

    async def _log(
        self, model, purpose, started, ok, tokens=(0, 0, 0), units=None, error=None, billed=None
    ) -> float:
        """`billed` overrides `ok` for cost: a voice stream that fails after audio was processed is still paid."""
        tokens_in, tokens_out, cached = tokens
        cost = (
            cost_usd(model, tokens_in, tokens_out, cached, units)
            if (ok if billed is None else billed)
            else 0.0
        )
        fields = {
            "model": model,
            "purpose": purpose,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(cost, 8),
            "latency_ms": max(_ms() - started, 0),
            "ok": ok,
            "error": error,
        }
        if units:
            fields["units"] = units
        await self.emit(fields)
        return cost

    async def _guard(self, model: str, purpose: str) -> None:
        if model in BLOCKED:
            raise ModelError(BLOCKED[model])
        try:
            await self.meter.check(self.session_id)
        except BudgetExceeded as exc:
            log.error("model call blocked (%s): %s", purpose, exc)
            await self._log(model, purpose, _ms(), ok=False, error=exc.reason)
            raise

    async def _post(self, model: str, purpose: str, path: str, body: dict, units=None):
        """POST with backoff on 429/5xx (Retry-After honoured). Returns (reply_body, cost, start, end)."""
        for attempt in range(MAX_ATTEMPTS):
            await self._guard(model, purpose)
            started = _ms()
            reply = await self.transport.post(path, body)
            if reply.status == 200:
                cost = await self._log(model, purpose, started, True, usage(reply.body), units)
                return reply.body, cost, started, _ms()
            await self._log(model, purpose, started, False, error=f"http_{reply.status}")
            if reply.status not in RETRY_STATUSES or attempt == MAX_ATTEMPTS - 1:
                raise ModelError(f"{model} {path} -> HTTP {reply.status}: {str(reply.body)[:300]}")
            retry_after = reply.headers.get("retry-after") or reply.headers.get("Retry-After")
            delay = (
                float(retry_after)
                if retry_after and str(retry_after).replace(".", "", 1).isdigit()
                else 0.5 * 2**attempt
            )
            await self.sleep(min(delay, 30))
        raise AssertionError("unreachable")

    async def responses(
        self,
        input,
        schema: dict,
        purpose: str,
        model: str | None = None,
        name: str = "output",
        reasoning: str | None = None,
    ) -> Result:
        """Structured output (json_schema, strict). Invalid output is retried once, then SchemaError.

        Strict schemas: root is an object, every object has `additionalProperties: false` and lists all its
        properties in `required`. `reasoning` sets `reasoning.effort` (minimal, low, medium, high, xhigh;
        "none" is refused by Muse Spark); omitted, the model reasons by default.
        """
        model = model or self.models["spark"]
        validator = Draft202012Validator(schema)
        body = {
            "model": model,
            "input": input,
            "text": {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
        }
        if reasoning:
            body["reasoning"] = {"effort": reasoning}
        total, last_error = 0.0, None
        for _ in range(2):
            raw, cost, start, end = await self._post(model, purpose, "/responses", body)
            total += cost
            try:
                data = json.loads(output_text(raw))
                errors = list(validator.iter_errors(data))
                if not errors:
                    return Result(data, raw, total, end - start, start, end)
                last_error = errors[0].message
            except json.JSONDecodeError as exc:
                last_error = f"not JSON: {exc}"
            log.warning("%s: output failed the schema (%s)", purpose, last_error)
        raise SchemaError(f"{purpose}: output failed the schema twice ({last_error})")

    async def sam(
        self, image_url: str, prompt: str, purpose: str = "segment", mask_encoding: str = "lossless"
    ) -> Result:
        """SAM 3.1 on one image with one short noun phrase; image_url may be a data: URL.

        `data` is `parse_segmentation(output text)`: one record per object found, [] when nothing matches.
        """
        model = self.models["sam"]
        body = {
            "model": model,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }
            ],
            "stream": False,
            "metadata": {"include_confidence": "true", "mask_encoding": mask_encoding},
        }
        raw, cost, start, end = await self._post(model, purpose, "/responses", body, units={"images": 1})
        return Result(parse_segmentation(output_text(raw)), raw, cost, end - start, start, end)

    async def image(self, prompt: str, purpose: str = "image") -> Result:
        """Muse Image generation. Out of P0: present so the interface is complete, not used by the demo."""
        model = self.models["image"]
        raw, cost, start, end = await self._post(
            model, purpose, "/images/generations", {"model": model, "prompt": prompt}, units={"images": 1}
        )
        return Result(raw, raw, cost, end - start, start, end)

    async def voice_stream(
        self,
        pcm: AsyncIterator[bytes],
        purpose: str = "transcribe",
        sample_rate: int = 24000,
        config: dict | None = None,
    ) -> AsyncIterator[dict]:
        """Realtime transcription over WebSocket; yields every server message as a dict.

        Protocol (dev.meta.ai/docs/api-reference/voice/realtime): the first text frame is the handshake, with
        the key inside it (the endpoint ignores the Authorization header); the reply is `{"sessionId"}`; audio
        goes as binary PCM16 LE mono frames (about 80 ms each, paced near real time, never more than 5 s
        ahead); `{"type": "endStream"}` ends it and the server closes with 1000 after the last events.
        Messages: transcript{transcript, final, audioProcessedMs}, speechStart/speechEnd{turnId},
        speechComplete{turnId, transcript} (ENDPOINTING, DIARIZATION), speaker{label}, audioProgress, error.
        `config` adds handshake fields: mode (PUSH_TO_TALK, ENDPOINTING, DIARIZATION), partialMode
        (CUMULATIVE, DELTA), keywords [str], languageBias [language names; "vi"/"en" are mapped].
        Cost is logged once when the stream ends, on whole seconds of audio processed.
        """
        import websockets

        if sample_rate not in VOICE_ENCODINGS:
            raise ValueError(f"sample_rate must be one of {sorted(VOICE_ENCODINGS)}")
        model = self.models["voice"]
        await self._guard(model, purpose)
        config = dict(config or {})
        bias = config.get("languageBias")
        if bias:
            config["languageBias"] = [
                LANGUAGE_NAMES.get(x, x) for x in ([bias] if isinstance(bias, str) else bias)
            ]
        handshake = {
            "authorization": {"accessToken": f"Bearer {env('MODEL_API_KEY')}"},
            "audioEncoding": VOICE_ENCODINGS[sample_rate],
            "model": model,
            "mode": "PUSH_TO_TALK",
            "emitAudioProgress": False,
            **config,
        }
        url = env("MODEL_VOICE_WS_URL", "wss://api.meta.ai/v1/asr/realtime")
        started, sent_bytes, processed_ms, ok, error = _ms(), 0, 0, True, None
        try:
            async with websockets.connect(url) as ws:
                await ws.send(json.dumps(handshake))

                async def pump():
                    nonlocal sent_bytes
                    async for chunk in pcm:
                        sent_bytes += len(chunk)
                        await ws.send(chunk)
                    await ws.send(json.dumps({"type": "endStream"}))

                sender = asyncio.create_task(pump())
                try:
                    async for message in ws:
                        if not isinstance(message, str):
                            continue
                        msg = json.loads(message)
                        processed_ms = max(processed_ms, int(msg.get("audioProcessedMs") or 0))
                        if msg.get("type") == "error":
                            ok, error = False, msg.get("errorCode") or msg.get("errorType") or "error"
                            log.error("%s: voice error %s", purpose, msg.get("message"))
                        yield msg
                finally:
                    sender.cancel()
        except Exception as exc:
            ok, error = False, error or type(exc).__name__
            raise
        finally:
            seconds = (processed_ms // 1000) or sent_bytes // (2 * sample_rate)
            await self._log(
                model, purpose, started, ok, units={"audio_seconds": seconds}, error=error, billed=seconds > 0
            )
