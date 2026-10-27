"""worker/voice: realtime messages (ENDPOINTING) -> final speech events with C8 timing."""

import pytest

from app.contracts import now_ms
from worker.media.__main__ import Poster
from worker.voice.transcribe import VoiceError, read_message, transcribe


@pytest.mark.parametrize(
    ("msg", "expected"),
    [
        ({"type": "speechComplete", "turnId": 3, "transcript": " Giá 450 nghìn. ", "audioProcessedMs": 3600},
         ("Giá 450 nghìn.", 3, 3600)),
        ({"type": "speechComplete", "transcript": "size 38"}, ("size 38", None, None)),
    ],
)  # fmt: skip
def test_speech_complete_is_the_final_text(msg, expected):
    h = read_message(msg)
    assert (h.text, h.turn, h.end_ms) == expected


@pytest.mark.parametrize(
    "msg",
    [
        {"sessionId": "s-1"},  # handshake ack
        {"type": "speechStart", "turnId": 1, "audioProcessedMs": 1200},
        {"type": "transcript", "transcript": "giá bốn", "final": False, "audioProcessedMs": 2400},
        {"type": "speechEnd", "turnId": 1, "audioProcessedMs": 3600},
        {"type": "speechComplete", "turnId": 1, "transcript": "   "},
    ],
)
def test_everything_else_is_ignored(msg):
    assert read_message(msg) is None


class FakeVoice:
    """Consumes the PCM like the WebSocket would and answers with scripted messages after given chunks."""

    def __init__(self, script: dict[int, dict]):
        self.script, self.config = script, None

    async def voice_stream(self, pcm, purpose, config):
        self.config = config
        n = 0
        async for _ in pcm:
            n += 1
            if n in self.script:
                yield self.script[n]


async def chunks(n: int):
    for _ in range(n):
        yield b"\x00" * 4800  # 100 ms of 24 kHz s16le mono


async def test_completed_turns_become_speech_events_with_timing(mem_app):
    _, store, client = mem_app
    voice = FakeVoice({
        3: {"type": "speechStart", "turnId": 1, "audioProcessedMs": 200},
        5: {"type": "transcript", "transcript": "đôi này", "final": False, "audioProcessedMs": 500},
        10: {"type": "speechComplete", "turnId": 1, "transcript": "Đôi này 450 nghìn.", "audioProcessedMs": 900},
        12: {"type": "speechStart", "turnId": 2, "audioProcessedMs": 1200},
        20: {"type": "speechComplete", "turnId": 2, "transcript": "Size 38, 39."},
    })  # fmt: skip
    t0 = now_ms() - 10_000
    poster = Poster("http://test", "voice1", http=client)
    posted = await transcribe(voice, chunks(25), ["Sneaker trắng"], "vi", lambda pos: t0 + pos, poster.post)
    assert posted == 2
    assert voice.config == {
        "mode": "ENDPOINTING",
        "keywords": ["Sneaker trắng"],
        "languageBias": ["Vietnamese"],
    }
    first, second = await store.read("voice1", 0, 10)
    assert (first["text"], first["start_ms"], first["end_ms"]) == ("Đôi này 450 nghìn.", t0 + 200, t0 + 900)
    assert (second["start_ms"], second["end_ms"], second["ts_ms"]) == (t0 + 1200, t0 + 1900, t0 + 1200)
    for e in (first, second):
        assert e["type"] == "speech" and e["final"] is True and e["lang"] == "vi"
        assert set(e["timing"]) == {"received_ms", "infer_start_ms", "infer_end_ms", "emitted_ms"}
        assert e["timing"]["received_ms"] == e["end_ms"] <= e["timing"]["infer_end_ms"]


async def test_error_message_ends_the_stream():
    voice = FakeVoice({2: {"type": "error", "errorCode": "INVALID_REQUEST", "message": "bad handshake"}})

    async def post(event):
        raise AssertionError("nothing is posted")

    with pytest.raises(VoiceError, match="INVALID_REQUEST"):
        await transcribe(voice, chunks(5), [], "vi", lambda pos: pos, post)
