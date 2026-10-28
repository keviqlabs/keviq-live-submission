"""Unpaid C5 live run: the worker's stand-ins are stored test1 output (gold labels, stored cut-outs of the same
call), never a model; a receipt's image is RUN only with its whole provenance."""

import asyncio
import base64
import hashlib
import json

import pytest

from measure import c5, c5_dry, c5_live, c5_offline, c5_proc
from measure.c5_score import Run, image_source
from measure.c5_shop import Gold
from worker.vision import sam

T0 = 1_793_000_000_000


def gold() -> Gold:
    return Gold(json.loads((c5.C5 / "windows.json").read_text()), {})


def sam_dir(tmp_path, rows):
    (tmp_path / "sam").mkdir()
    for r in rows:
        if r.pop("file", True):
            (tmp_path / "sam" / f"cut_{r['t']:03d}_{r['product']}.png").write_bytes(f"png{r['t']}".encode())
    (tmp_path / "sam.json").write_text(json.dumps({"rows": rows}))
    return tmp_path / "sam.json"


def test_only_decoded_cutouts_with_a_file_are_stored(tmp_path):
    path = sam_dir(tmp_path, [{"t": 24, "product": "SP01", "noun": "shoe", "mask_decoded": True},
                              {"t": 30, "product": "SP01", "noun": "shoe", "mask_decoded": False},
                              {"t": 36, "product": "SP01", "noun": "shoe"},
                              {"t": 67, "product": "SP02", "noun": "shoe", "mask_decoded": True, "file": False}])  # fmt: skip
    stored = c5_offline.stored_cutouts(path)
    assert {t: [(r["noun"], r["sku"], r["png"].name) for r in v] for t, v in stored.items()} == {
        24: [("shoe", "SP01", "cut_024_SP01.png")]
    }


@pytest.fixture
def no_gold(monkeypatch):
    """Choosing an image never reads the gold: any read of it fails the test."""
    for name in ("__init__", "load", "held_at", "card_item", "prices", "price_at", "right_price"):
        monkeypatch.setattr(Gold, name, lambda *a, **k: pytest.fail("the gold was read to choose an image"))


def cut(tmp_path, t, sku, noun="sneaker"):
    png = tmp_path / f"cut_{t:03d}_{sku}.png"
    png.write_bytes(f"png {t} {sku}".encode())
    return {"noun": noun, "sku": sku, "png": png}


def cuts(stored):
    out = c5_offline.Cutouts(stored, sam.noun)
    for i in (5, 30, 58):
        out.note({"jpeg_path": f"data/local/test1/frames/f_{i:05d}.jpg"}, f"jpeg{i}".encode())
    return out


def test_a_frame_gets_the_stored_cutout_of_the_same_frame_and_noun_whatever_its_label(tmp_path, no_gold):
    stored = {30: [cut(tmp_path, 30, "SP09")], 24: [cut(tmp_path, 24, "SP01")]}  # SP09: not a sneaker
    png, row = cuts(stored).pick(b"jpeg30", "sneaker")
    assert png.name == "cut_030_SP09.png" and row == {
        "source_session": "test1", "frame": 30, "frame_sha256": hashlib.sha256(b"jpeg30").hexdigest(),
        "category": "sneaker", "noun": "sneaker", "source_t": 30, "stored_sku": "SP09", "png": "cut_030_SP09.png",
        "png_sha256": hashlib.sha256(b"png 30 SP09").hexdigest(), "reason": None,
    }  # fmt: skip


@pytest.mark.parametrize("jpeg, category, reason", [
    (b"never", "sneaker", "frame not seen by this worker"),
    (b"jpeg5", "sneaker", "no stored cut-out of frame 5 for 'sneaker'"),  # one at 24: the nearest is not taken
    (b"jpeg30", "shirt", "no stored cut-out of frame 30 for 'shirt'"),  # the noun is an input of the call
    (b"jpeg58", "sneaker", "several stored cut-outs of frame 58 for 'sneaker'"),
])  # fmt: skip
def test_no_stored_cutout_of_the_same_call_means_no_image_and_a_reason(
    tmp_path, no_gold, jpeg, category, reason
):
    stored = {24: [cut(tmp_path, 24, "SP01")], 30: [cut(tmp_path, 30, "SP01")],
              58: [cut(tmp_path, 58, "SP01"), cut(tmp_path, 58, "SP02")]}  # fmt: skip
    png, row = cuts(stored).pick(jpeg, category)
    assert (png, row["png"], row["png_sha256"], row["source_t"], row["reason"]) == (
        None,
        None,
        None,
        None,
        reason,
    )


def test_install_puts_gold_labels_and_stored_cutouts_in_the_worker(monkeypatch, tmp_path, no_gold):
    from worker import runner
    from worker.brain import card, labels
    from worker.vision import frames

    for mod, name in (
        (labels.LABELER, "client_for"),
        (sam, "cut_out"),
        (frames, "load"),
        (runner.Runner, "client_for"),
    ):
        monkeypatch.setattr(mod, name, getattr(mod, name))  # undone after the test
    (tmp_path / "labels.json").write_text(json.dumps({"products": {}}))
    monkeypatch.setattr(c5, "LABELS", tmp_path / "labels.json")
    (tmp_path / "cut.png").write_bytes(b"stored png")
    stored = {30: [{"noun": "sneaker", "sku": "SP01", "png": tmp_path / "cut.png"}]}
    monkeypatch.setattr(c5_offline, "stored_cutouts", lambda: stored)
    said = []
    info = c5_offline.install(3, lambda kind, **f: said.append((kind, f)))
    assert info["loop"] == 3 and info["gold_rows"] == len(c5.streams(3)[0]) and info["cutout_frames"] == [30]
    client = labels.LABELER.client_for(None)
    assert isinstance(client, c5_dry.GoldClient) and set(client.rows) == {
        r["comment_id"] for r in c5.streams(3)[0]
    }

    jpeg = b"\xff\xd8 frame 30"
    event = {"jpeg_b64": base64.b64encode(jpeg).decode(), "jpeg_path": "data/local/test1/frames/f_00030.jpg"}
    assert frames.load(event) == jpeg
    ctx = runner.Ctx({"session_id": "s", "event_id": "e"}, runner.Runner("http://127.0.0.1:9", {}))
    url, (start, end) = asyncio.run(
        sam.cut_out(ctx.client(), jpeg, "sneaker")
    )  # as card.attach_cutout calls it
    assert url == "data:image/png;base64," + base64.b64encode(b"stored png").decode() and start == end
    assert asyncio.run(sam.cut_out(ctx.client(), jpeg, "shirt")) is None
    with pytest.raises(RuntimeError, match="no model client"):
        ctx.client().responses([], {})
    spoken, _ = asyncio.run(card.read_speech("hai trăm nghìn", ctx))  # the rule reader, as with no key
    assert spoken.price == 200_000
    assert [(k, f["frame"], f["stored_sku"], f["png_sha256"]) for k, f in said] == [
        ("cutout", 30, "SP01", hashlib.sha256(b"stored png").hexdigest()),
        ("cutout", 30, None, None),
    ]


def receipt_run(image: bytes, cutouts: list[dict], frame_ts: int = T0 + 30_000) -> Run:
    url = "data:image/png;base64," + base64.b64encode(image).decode()
    card = {
        "type": "card",
        "card_id": "c1",
        "status": "live",
        "price": 1,
        "provenance": {"frame_ts_ms": T0 + 30_000},
    }
    events = [
        {"event_id": "f30", "type": "frame", "ts_ms": frame_ts, "jpeg_path": "data/local/test1/frames/f_00030.jpg"},
        {"event_id": "ps1", "type": "product_seen", "ts_ms": T0 + 30_500, "frame_ts_ms": T0 + 30_000,
         "caused_by": ["f30"]},
        {**card, "event_id": "c1v1", "ts_ms": T0 + 31_000, "version": 1, "image_url": None, "caused_by": ["ps1"]},
        {**card, "event_id": "c1v2", "ts_ms": T0 + 32_000, "version": 2, "image_url": url,
         "caused_by": ["sp1", "f30"]},
    ]  # fmt: skip
    return Run(events, [{"k": 1, "t0_ms": T0, "cutouts": cutouts}], gold())


def test_a_receipt_image_is_run_only_with_its_whole_provenance():
    logged = {"kind": "cutout", "source_session": "test1", "frame": 30, "source_t": 30,
              "png_sha256": hashlib.sha256(b"png").hexdigest()}  # fmt: skip
    receipt = {"card_id": "c1", "card_version": 2, "image_url": "data:image/png;base64,cG5n"}  # b"png"
    src = image_source(receipt_run(b"png", [logged]), receipt)
    assert src == {
        "card_id": "c1", "card_version": 2, "product_seen": "ps1", "frame_ts_ms": T0 + 30_000, "frame_event": "f30",
        "source_frame": 30, "image_sha256": hashlib.sha256(b"png").hexdigest(), "source_session": "test1",
        "source_t": 30, "image_path": "RUN", "missing": [],
    }  # fmt: skip
    assert image_source(receipt_run(b"png", []), receipt)["missing"] == ["source_session", "source_t"]
    other = image_source(receipt_run(b"png", [{**logged, "frame": 31}]), receipt)  # another frame's cut-out
    assert (other["image_path"], other["missing"]) == ("NOT_RUN", ["source_session", "source_t"])
    late = image_source(receipt_run(b"png", [logged], frame_ts=T0 + 31_000), receipt)  # not the opening frame
    assert (late["image_path"], late["missing"]) == ("NOT_RUN", ["frame_event", "source_frame", "source_session",
                                                                 "source_t"])  # fmt: skip
    none = image_source(receipt_run(b"png", [logged]), {**receipt, "image_url": None})
    assert (none["image_path"], none["missing"]) == (
        "NOT_RUN",
        ["image_sha256", "source_session", "source_t"],
    )


def test_the_stand_ins_refuse_a_model_key(monkeypatch):
    monkeypatch.setattr(c5_proc, "proof", lambda module: {"offline_loop": 1, "model_key_set": True})
    monkeypatch.setattr(c5_proc, "say", lambda kind, **f: None)
    monkeypatch.setattr(c5_offline, "install", lambda *a: pytest.fail("installed with a key set"))
    monkeypatch.setattr(c5_proc.runpy, "run_module", lambda *a, **k: pytest.fail("worker started"))
    monkeypatch.setattr(c5_proc.sys, "argv", ["c5_proc", "worker"])
    with pytest.raises(SystemExit, match="unpaid runs only"):
        c5_proc.main()


def test_the_stored_lines_are_final_speech_when_they_end(monkeypatch, tmp_path):
    voice = tmp_path / "voice.json"
    voice.write_text(json.dumps({"rows": [{"start_s": 1.0, "end_s": 2.5, "text": "a"},
                                          {"start_s": 9.0, "end_s": 11.0, "text": "b"}]}))  # fmt: skip
    monkeypatch.setattr(c5_dry, "VOICE", voice)
    items = c5_live.replay_seen("run", 2, T0, 10_000, "speech")
    assert [(due - T0, ev["ts_ms"] - T0, ev["end_ms"] - T0, ev["final"]) for due, ev in items] == [
        (2500 + c5_dry.SPEECH_LAG_MS, 1000, 2500, True),
        (11000 + c5_dry.SPEECH_LAG_MS, 9000, 11000, True),
    ]
    assert [ev["ts_ms"] - T0 for _, ev in c5_live.replay_seen("run", 2, T0, 9000, "speech")] == [1000]
    assert all(ev["type"] == "product_seen" for _, ev in c5_live.replay_seen("run", 2, T0, 10_000))
