"""Unpaid C5 live run: the worker's stand-ins are stored test1 output (gold labels, stored cut-outs), never a model."""

import asyncio
import base64
import json

import pytest

from measure import c5, c5_dry, c5_live, c5_offline, c5_proc
from measure.c5_shop import Gold

T0 = 1_793_000_000_000
CATALOG = json.loads((c5.C5 / "catalog.json").read_text())


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
    path = sam_dir(tmp_path, [{"t": 24, "product": "SP01", "mask_decoded": True},
                              {"t": 30, "product": "SP01", "mask_decoded": False},
                              {"t": 36, "product": "SP01"},
                              {"t": 67, "product": "SP02", "mask_decoded": True, "file": False}])  # fmt: skip
    assert {k: [t for t, _ in v] for k, v in c5_offline.stored_cutouts(path).items()} == {"SP01": [24]}


def cuts(stored):
    out = c5_offline.Cutouts(gold(), CATALOG, stored)
    for i in (5, 30, 58, 100, 140, 270):
        out.note({"jpeg_path": f"data/local/test1/frames/f_{i:05d}.jpg"}, f"jpeg{i}".encode())
    return out


def test_a_frame_gets_the_nearest_stored_cutout_of_the_item_held_at_it(tmp_path):
    out = cuts({"SP01": [(24, tmp_path / "a.png"), (36, tmp_path / "b.png"), (47, tmp_path / "c.png")]})
    png, row = out.pick(b"jpeg30", "sneaker")
    assert (png.name, row["frame"], row["item"], row["held"], row["source_t"], row["reason"]) == (
        "a.png",
        30,
        "SP01",
        "SP01",
        24,
        None,
    )
    png, row = out.pick(b"jpeg30", "sandal")  # another label of the same item (membership)
    assert png.name == "a.png"


def test_a_frame_in_a_change_band_gets_the_cutout_of_the_card_item_not_of_the_one_still_held(tmp_path):
    out = cuts({"SP01": [(47, tmp_path / "sp01.png")], "SP02": [(67, tmp_path / "sp02.png")]})
    png, row = out.pick(b"jpeg58", "sneaker")  # 58 s: SP01 held until 58.6 s, SP02's band from 49.4 s
    assert (png.name, row["item"], row["held"], row["source_t"]) == ("sp02.png", "SP02", "SP01", 67)


@pytest.mark.parametrize("jpeg, category, reason", [
    (b"jpeg30", "shirt", "item SP01 is not of category shirt"),
    (b"jpeg5", "sneaker", "no item for a card at this frame"),
    (b"jpeg100", "sneaker", "no stored cut-out of SP03 on this machine"),
    (b"jpeg270", "other", "no stored cut-out of SP09 on this machine"),
    (b"never", "sneaker", "frame not seen by this worker"),
])  # fmt: skip
def test_no_stored_cutout_means_no_image_and_a_reason(tmp_path, jpeg, category, reason):
    png, row = cuts({"SP01": [(24, tmp_path / "a.png")], "SP04": [(136, tmp_path / "d.png")]}).pick(
        jpeg, category
    )
    assert (png, row["png"], row["reason"]) == (None, None, reason)


def test_install_puts_gold_labels_and_stored_cutouts_in_the_worker(monkeypatch, tmp_path):
    from worker import runner
    from worker.brain import card, labels
    from worker.vision import frames, sam

    for mod, name in (
        (labels.LABELER, "client_for"),
        (sam, "cut_out"),
        (frames, "load"),
        (runner.Runner, "client_for"),
    ):
        monkeypatch.setattr(mod, name, getattr(mod, name))  # undone after the test
    (tmp_path / "labels.json").write_text(json.dumps({"products": {}}))
    monkeypatch.setattr(c5, "LABELS", tmp_path / "labels.json")
    monkeypatch.setattr(card, "CATALOG", CATALOG)  # the worker of a C5 run has CATALOG_PATH = the c5 catalog
    (tmp_path / "cut.png").write_bytes(b"stored png")
    monkeypatch.setattr(c5_offline, "stored_cutouts", lambda: {"SP01": [(24, tmp_path / "cut.png")]})
    said = []
    info = c5_offline.install(3, lambda kind, **f: said.append((kind, f)))
    assert (
        info["loop"] == 3 and info["gold_rows"] == len(c5.streams(3)[0]) and info["cutout_items"] == ["SP01"]
    )
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
    assert [(k, f["item"], f["png"]) for k, f in said] == [
        ("cutout", "SP01", "cut.png"),
        ("cutout", "SP01", None),
    ]


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
