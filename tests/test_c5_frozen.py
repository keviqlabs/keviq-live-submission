"""--observations: a stored run's product_seen and speech rows are replayed on the loop clock, valid by contract."""

import json

import pytest
from jsonschema import ValidationError

from measure import c5, c5_frozen, c5_live

T0_STORED = 1_793_176_264_452  # the stored run's first frame, index 0
T0_LOOP = 1_800_000_000_000


def observations(tmp_path, with_frame=True):
    rows = []
    if with_frame:
        rows.append({"event_id": "d.f3", "session_id": "d", "type": "frame", "ts_ms": T0_STORED + 3000,
                     "jpeg_path": "data/local/test1/frames/f_00003.jpg", "v": 1, "source": "media"})  # fmt: skip
    ts = T0_STORED + 83_200
    rows.append({"event_id": "d.ps-9", "session_id": "d", "type": "product_seen", "ts_ms": ts, "v": 1, "seq": 7,
                 "source": "worker.vision", "ingested_ms": ts + 1, "caused_by": ["d.f83"], "frame_ts_ms": ts,
                 "category": "shoes", "confidence": 0.91, "is_new": True,
                 "attributes": {"color": "white", "size": None, "material": None},
                 "timing": {"received_ms": ts, "infer_start_ms": ts + 10, "infer_end_ms": ts + 900,
                            "emitted_ms": ts + 905}})  # fmt: skip
    start = T0_STORED + 90_000
    rows.append({"event_id": "d.u9", "session_id": "d", "type": "speech", "ts_ms": start, "v": 1, "seq": 8,
                 "source": "media.voice", "ingested_ms": start + 3000, "text": "đôi này giá hai trăm",
                 "final": True, "lang": "vi", "start_ms": start, "end_ms": start + 2500,
                 "timing": {"received_ms": start + 2500, "infer_start_ms": start + 2500,
                            "infer_end_ms": start + 2600, "emitted_ms": start + 2600}})  # fmt: skip
    path = tmp_path / "observations.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_rows_keep_their_offset_from_the_stored_clock(tmp_path):
    out = c5_frozen.items(observations(tmp_path), "run", 2, T0_LOOP)
    assert [ev["type"] for _, ev in out] == ["product_seen", "speech"]
    due, ps = out[0]
    assert ps["event_id"] == "run.ps-2-00" and ps["session_id"] == "run" and ps["source"] == "replay"
    assert ps["ts_ms"] == T0_LOOP + 83_200 and ps["frame_ts_ms"] == ps["ts_ms"]
    assert ps["timing"]["emitted_ms"] == T0_LOOP + 83_200 + 905 == due
    assert ps["attributes"] == {"color": "white", "size": None, "material": None} and ps["is_new"] is True
    assert "caused_by" not in ps and "seq" not in ps and "ingested_ms" not in ps
    due, sp = out[1]
    assert sp["event_id"] == "run.u2-00" and sp["start_ms"] == T0_LOOP + 90_000 == sp["ts_ms"]
    assert sp["end_ms"] == T0_LOOP + 92_500 and sp["text"] == "đôi này giá hai trăm"
    assert due == T0_LOOP + 92_600 + c5_frozen.SPEECH_LAG_MS


def test_replay_seen_takes_the_observations_and_the_loop_cut(tmp_path):
    path = observations(tmp_path)
    seen = c5_live.replay_seen("run", 1, T0_LOOP, 100_000, observations=path)
    speech = c5_live.replay_seen("run", 1, T0_LOOP, 100_000, "speech", path)
    assert [ev["event_id"] for _, ev in seen] == ["run.ps-1-00"]
    assert [ev["event_id"] for _, ev in speech] == ["run.u1-00"]
    assert c5_live.replay_seen("run", 1, T0_LOOP, 80_000, observations=path) == []


def test_no_frame_means_no_clock(tmp_path):
    with pytest.raises(ValueError, match="no frame"):
        c5_frozen.items(observations(tmp_path, with_frame=False), "run", 1, T0_LOOP)


def test_a_row_off_contract_is_refused(tmp_path):
    path = observations(tmp_path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    del rows[1]["category"]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    with pytest.raises((ValidationError, KeyError)):
        c5_frozen.items(path, "run", 1, T0_LOOP)


def test_cli_flag_is_live_and_unpaid_only(tmp_path):
    path = observations(tmp_path)
    with pytest.raises(SystemExit):
        c5.main(["--dry", "--observations", str(path)])
    with pytest.raises(SystemExit):
        c5.main(["--base-url", "http://127.0.0.1:1", "--paid", "--observations", str(path)])
    with pytest.raises(SystemExit):
        c5.main(["--base-url", "http://127.0.0.1:1", "--observations", str(tmp_path / "missing.jsonl")])
