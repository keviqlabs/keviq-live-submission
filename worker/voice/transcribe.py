"""Voice Transcribe over WebSocket -> final `speech` events (C8 timing on each).

Audio is not an event, so the media process drives this: it hands over PCM chunks and posts what comes back.
The session runs in ENDPOINTING mode (dev.meta.ai/docs/speech-to-text, read 2026-10-27, D-019): the server
opens a turn with `speechStart`, sends partial `transcript`s and closes it with `speechComplete`, whose
`transcript` is the final text. Only `speechComplete` becomes an event. Messages carry `audioProcessedMs`
(processing progress, not word times), so a turn runs from its speechStart's position to its speechComplete's;
a missing position is filled from the audio sent so far. An `error` message ends the stream with VoiceError
(the client's handshake and error handling are in meta/client.py, D-014).
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from app.contracts import now_ms
from worker.media.extract import SAMPLE_RATE

LANGUAGES = {"vi": "Vietnamese", "en": "English"}  # languageBias takes language names, not codes


class VoiceError(RuntimeError):
    pass


@dataclass
class Heard:
    text: str
    turn: int | None
    end_ms: int | None


def read_message(msg: dict) -> Heard | None:
    """A turn's final text; partials, speechStart/speechEnd and the handshake ack give None."""
    text = msg.get("transcript")
    if msg.get("type") != "speechComplete" or not isinstance(text, str) or not text.strip():
        return None
    return Heard(text.strip(), msg.get("turnId"), msg.get("audioProcessedMs"))


class AudioClock:
    """Wraps the PCM stream and remembers how much audio (ms) has gone out."""

    def __init__(self, pcm: AsyncIterator[bytes], sample_rate: int = SAMPLE_RATE):
        self.pcm, self.sample_rate = pcm, sample_rate
        self.sent_ms = 0

    async def __aiter__(self):
        async for chunk in self.pcm:
            yield chunk
            self.sent_ms += len(chunk) * 1000 // (2 * self.sample_rate)


async def transcribe(
    client,
    pcm: AsyncIterator[bytes],
    keywords: list[str],
    lang: str,
    to_wall: Callable[[int], int],
    post: Callable[[dict], Awaitable[None]],
) -> int:
    """Stream audio, post one partial `speech` event per completed turn; returns how many were posted.

    `to_wall(audio_ms)` maps a position in the recording to the session clock (start + pos / speed).
    """
    clock, posted, prev_end, starts = AudioClock(pcm), 0, 0, {}
    config = {"mode": "ENDPOINTING", "keywords": keywords, "languageBias": [LANGUAGES.get(lang, lang)]}
    async for msg in client.voice_stream(clock, purpose="transcribe", config=config):
        if not isinstance(msg, dict):
            continue
        if msg.get("type") == "error":
            raise VoiceError(str(msg.get("errorCode") or msg.get("message") or "error")[:200])
        if msg.get("type") == "speechStart":
            starts[msg.get("turnId")] = msg.get("audioProcessedMs")
            continue
        heard = read_message(msg)
        if not heard:
            continue
        arrived = now_ms()
        end = heard.end_ms if heard.end_ms is not None else clock.sent_ms
        start = starts.pop(heard.turn, None)
        start = min(start if start is not None else prev_end, end)
        prev_end = end
        sent = min(to_wall(end), arrived)  # when the turn's last audio was available (session clock)
        await post(
            {
                "type": "speech",
                "ts_ms": to_wall(start),
                "text": heard.text,
                "final": True,
                "lang": lang,
                "start_ms": to_wall(start),
                "end_ms": to_wall(end),
                "timing": {
                    "received_ms": sent,
                    "infer_start_ms": sent,
                    "infer_end_ms": arrived,
                    "emitted_ms": now_ms(),
                },
            }
        )
        posted += 1
    return posted
