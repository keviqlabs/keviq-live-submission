"""Key empty: the live runner posts the 6b replay rows as product_seen caused by the run's own frames."""

import asyncio
import inspect
import json
import time

from app.contracts import make_event
from measure import c5_dry, c5_live

T0 = 1_793_000_000_000


def frame(i, sid="run"):
    return {"event_id": f"{sid}.f{i}", "session_id": sid, "type": "frame", "ts_ms": T0 + i * 1000,
            "jpeg_path": f"data/x/f_{i:05d}.jpg"}  # fmt: skip


def seen(t, sid="run"):
    ts = T0 + t * 1000
    return make_event("product_seen", sid, ts, "replay", event_id=f"{sid}.ps-1-{t:02d}", category="shoes",
                      confidence=0.9, attributes={"color": None, "size": None, "material": None}, is_new=True,
                      frame_ts_ms=ts)  # fmt: skip


class Http:
    def __init__(self):
        self.posted = []

    async def post(self, url, json):
        self.posted.extend(json)
        return self

    def raise_for_status(self):
        pass


def test_the_replay_rows_are_product_seen_on_the_loops_clock(monkeypatch, tmp_path):
    voice = tmp_path / "voice.json"
    voice.write_text(json.dumps({"rows": []}))
    monkeypatch.setattr(c5_dry, "VOICE", voice)
    rows = [r for r in json.loads(c5_dry.VISION.read_text())["rows"] if r.get("is_new")]
    items = c5_live.replay_seen("run", 2, T0, 10**9)
    assert [ev["type"] for _, ev in items] == ["product_seen"] * len(rows)
    assert [(due - T0, ev["ts_ms"] - T0) for due, ev in items] == [
        (r["t"] * 1000 + r["ms"], r["t"] * 1000) for r in rows
    ]
    assert [ev["event_id"] for _, ev in items][:1] == ["run.ps-2-00"]
    cut = rows[len(rows) // 2]["t"] * 1000
    assert [ev["ts_ms"] - T0 for _, ev in c5_live.replay_seen("run", 2, T0, cut)] == [
        r["t"] * 1000 for r in rows if r["t"] * 1000 < cut
    ]


def test_a_label_is_caused_by_the_sessions_frame_at_its_time():
    out = c5_live.caused(seen(5), [frame(i) for i in range(10)] + [frame(5, "other")])
    assert (out["caused_by"], out["frame_ts_ms"], out["ts_ms"]) == (["run.f5"], T0 + 5000, T0 + 5000)
    assert out["type"] == "product_seen" and out["is_new"]


def test_a_label_without_its_own_frame_takes_the_frame_just_before():
    out = c5_live.caused(seen(3), [frame(0), frame(1), frame(2), frame(4)])
    assert (out["caused_by"], out["frame_ts_ms"], out["ts_ms"]) == (["run.f2"], T0 + 2000, T0 + 3000)


def test_a_label_before_any_frame_of_the_session_is_not_posted():
    assert c5_live.caused(seen(1), [frame(3), frame(4), frame(0, "other")]) is None
    assert c5_live.caused(seen(1), []) is None


def test_a_label_after_the_last_frame_takes_the_last_frame():
    out = c5_live.caused(seen(7), [frame(i) for i in range(5)])
    assert (out["caused_by"], out["frame_ts_ms"], out["ts_ms"]) == (["run.f4"], T0 + 4000, T0 + 7000)


def test_the_poster_uses_this_loops_frames_and_waits_for_the_labels_own_frame():
    async def scenario():
        frames = [frame(i) for i in range(3)]  # loop 1's frames come before `start`
        start = len(frames)
        frames += [frame(i) for i in range(10, 13)]
        http = Http()
        items = [(0, seen(9)), (0, seen(12)), (0, seen(14))]
        posting = asyncio.create_task(c5_live.post_seen(http, "run", items, frames, start, wait_s=0.5))
        await asyncio.sleep(0.15)
        frames.append(frame(14))  # the label's own frame, on its way through the watch
        return await posting, http.posted

    summary, posted = asyncio.run(scenario())
    assert summary == {"posted": 2, "no_frame": ["run.ps-1-09"]}
    assert [(p["event_id"], p["caused_by"]) for p in posted] == [("run.ps-1-12", ["run.f12"]),
                                                                 ("run.ps-1-14", ["run.f14"])]  # fmt: skip


def test_a_frame_that_never_comes_is_waited_for_at_most_wait_s():
    assert inspect.signature(c5_live.post_seen).parameters["wait_s"].default == 2.0

    async def scenario():
        http, frames = Http(), [frame(i) for i in range(5)]
        t = time.monotonic()
        summary = await asyncio.wait_for(
            c5_live.post_seen(http, "run", [(0, seen(7))], frames, 0, wait_s=0.3), 2
        )
        return summary, http.posted, time.monotonic() - t

    summary, posted, took = asyncio.run(scenario())
    assert summary == {"posted": 1, "no_frame": []} and [p["caused_by"] for p in posted] == [["run.f4"]]
    assert 0.3 <= took < 1.0
