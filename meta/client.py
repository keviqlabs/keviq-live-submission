"""Thin Model API client: every call is cap-checked first and logged as a model_call afterwards.

    client = MetaClient(transport, meter, emit)
    out = await client.responses(input=..., schema={...}, purpose="label_comments")

`emit(fields)` receives the model_call fields (model, purpose, tokens, cost, latency, ok, error, units);
the caller wraps them into an event for its session. Request and response shapes for SAM, Voice and the
structured-output fields are not fully documented yet (see docs/DECISIONS.md); parsing is defensive.
"""

import asyncio
import json
import logging
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


class MetaClient:
    def __init__(
        self,
        transport,
        meter: Meter,
        emit: Callable[[dict], Awaitable[None]],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.transport, self.meter, self.emit, self.sleep = transport, meter, emit, sleep
        self.models = {
            "spark": env("MODEL_SPARK", "muse-spark-1.3"),
            "sam": env("MODEL_SAM", "sam-3.1"),
            "voice": env("MODEL_VOICE", "muse-voice-transcribe-1.0"),
            "image": env("MODEL_IMAGE", "muse-image-1.0"),
        }

    async def _log(self, model, purpose, started, ok, tokens=(0, 0, 0), units=None, error=None) -> float:
        tokens_in, tokens_out, cached = tokens
        cost = cost_usd(model, tokens_in, tokens_out, cached, units) if ok else 0.0
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
            await self.meter.check()
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
        self, input, schema: dict, purpose: str, model: str | None = None, name: str = "output"
    ) -> Result:
        """Structured output (json_schema, strict). Invalid output is retried once, then SchemaError."""
        model = model or self.models["spark"]
        validator = Draft202012Validator(schema)
        body = {
            "model": model,
            "input": input,
            "text": {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
        }
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

    async def sam(self, image_url: str, prompt: str, purpose: str = "segment") -> Result:
        """SAM 3.1 segmentation with one short noun phrase; image_url may be a data: URL."""
        model = self.models["sam"]
        body = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }
            ],
        }
        raw, cost, start, end = await self._post(model, purpose, "/responses", body, units={"images": 1})
        return Result(raw, raw, cost, end - start, start, end)

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
        """Realtime transcription over WebSocket; yields server messages as dicts.

        The message protocol is not on the docs page we read; the config frame below is a placeholder
        that worker/voice must confirm against dev.meta.ai/docs/api-reference/voice/realtime.
        Cost is logged once when the stream ends: whole seconds of PCM16 mono audio sent.
        """
        import websockets

        model = self.models["voice"]
        await self._guard(model, purpose)
        url = env("MODEL_VOICE_WS_URL", "wss://api.meta.ai/v1/asr/realtime")
        started, sent_bytes, ok, error = _ms(), 0, True, None
        headers = {"Authorization": f"Bearer {env('MODEL_API_KEY')}"}
        try:
            async with websockets.connect(url, additional_headers=headers) as ws:
                await ws.send(json.dumps({"model": model, "sample_rate": sample_rate, **(config or {})}))

                async def pump():
                    nonlocal sent_bytes
                    async for chunk in pcm:
                        sent_bytes += len(chunk)
                        await ws.send(chunk)
                    await ws.send(json.dumps({"type": "end"}))

                sender = asyncio.create_task(pump())
                try:
                    async for message in ws:
                        if isinstance(message, str):
                            yield json.loads(message)
                finally:
                    sender.cancel()
        except Exception as exc:
            ok, error = False, type(exc).__name__
            raise
        finally:
            seconds = sent_bytes // (2 * sample_rate)
            await self._log(model, purpose, started, ok, units={"audio_seconds": seconds}, error=error)
