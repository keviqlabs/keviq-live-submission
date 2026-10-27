"""worker/media: 1 fps frames and 24 kHz PCM from a synthetic clip made in the test (no real media)."""

import shutil
import subprocess

import pytest

from app.contracts import now_ms
from worker.media import extract
from worker.media.__main__ import Poster, play_frames

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    out = tmp_path_factory.mktemp("media") / "synthetic.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=10:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )  # fmt: skip
    return out


async def test_frame_is_a_jpeg_with_the_long_side_at_most_640(clip, tmp_path):
    jpeg = await extract.frame_at(clip, 1.0, tmp_path / "f.jpg")
    assert extract.jpeg_size(jpeg) == (640, 360)
    assert (tmp_path / "f.jpg").read_bytes() == jpeg
    assert round(extract.duration_s(clip)) == 3


@pytest.fixture(scope="module")
def phone_clip(tmp_path_factory):
    out = tmp_path_factory.mktemp("media") / "phone.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=5:duration=1",
         "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )  # fmt: skip
    return out


async def test_portrait_phone_frame_is_360_by_640(phone_clip, tmp_path):
    assert extract.jpeg_size(await extract.frame_at(phone_clip, 0.5, tmp_path / "p.jpg")) == (360, 640)


async def test_pcm_is_s16le_mono_24khz_in_100ms_chunks(clip):
    chunks = [c async for c in extract.pcm_chunks(clip, speed=0)]
    assert {len(c) for c in chunks[:-1]} == {2 * extract.SAMPLE_RATE // 10}
    assert sum(len(c) for c in chunks) == pytest.approx(3 * 2 * extract.SAMPLE_RATE, rel=0.02)


async def test_frames_reach_the_store_with_c8_timing(mem_app, clip, tmp_path):
    _, store, client = mem_app
    t0 = now_ms()
    posted = await play_frames(
        clip, tmp_path / "frames", Poster("http://test", "media1", http=client), t0, 50, None
    )
    events = await store.read("media1", 0, 50)
    assert posted == 3 and [e["type"] for e in events] == ["frame"] * 3
    assert [e["ts_ms"] for e in events] == [t0, t0 + 20, t0 + 40]
    for e in events:
        assert set(e["timing"]) == {"received_ms", "infer_start_ms", "infer_end_ms", "emitted_ms"}
        assert (e["width"], e["height"]) == (640, 360) and e["jpeg_path"].endswith(".jpg")
