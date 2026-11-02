"""measure/manual: the scorer on logs built from the stream-1 gold, the local server's checks, and the CLI.

- a log that does what the gold says scores 28/28 items right, all taken up within 60 s, phones and links hidden;
- the scorer ignores actions after the lock and before a comment appeared, and the last order or ask counts;
- /data carries no gold field; the server refuses a second start, actions before start and after the lock;
- `score --dir` refuses a stream whose sha256 differs from the one the run handled.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.settings import ROOT
from measure.c5_score import counted
from measure.manual import __main__ as cli
from measure.manual.score import appears_ms, assistant, load_jsonl, score, table
from measure.manual.server import create_app, sha256

C5 = ROOT / "data" / "comments" / "c5"
STREAM = C5 / "stream-1.jsonl"
ROWS = load_jsonl(STREAM)
BY_ID = {r["comment_id"]: r for r in ROWS}
END_MS = 351_533
RUN = {"speed": 1.0, "tail_ms": 30_000}
GOLD_KEYS = (
    "label",
    "expected_action",
    "label_source",
    "reference_kind",
    "situations",
    "has_phone",
    "has_link",
)


def entry(kind, video_ms, comment_id=None, **picks):
    e = {"kind": kind, "video_ms": video_ms, "server_ms": 1_000 + video_ms}
    return (
        e
        | ({"comment_id": comment_id} if comment_id else {})
        | {k: v for k, v in picks.items() if v is not None}
    )


def frame(actions, end=END_MS, lock=True):
    """start, the actions in time order, end, and the lock 30 s after the end."""
    out = [entry("start", 0), *sorted(actions, key=lambda e: e["video_ms"]), entry("end", end)]
    return out + ([entry("lock", end + RUN["tail_ms"])] if lock else [])


def gold_actions(delay=5_000):
    """What the gold says, `delay` after each comment appeared: an order or an ask, and hides."""
    out = []
    for r in ROWS:
        at = appears_ms(r) + delay
        if r["label"] == "order" and counted(r):
            o = r["order"]
            kind = "ask" if r["expected_action"] == "ask_customer" else "order"
            out.append(entry(kind, at, r["comment_id"], item=o["product"], size=o["size"], qty=o["qty"]))
        elif r["label"] == "order":
            out.append(entry("ask", at, r["comment_id"], item="unclear", qty=1))
        if r["label"] in ("scam", "spam") or r["has_phone"] or r["has_link"]:
            out.append(entry("hide", at + 1_000, r["comment_id"]))
    return out


def test_a_log_that_follows_the_gold_scores_every_row_right():
    s = score(frame(gold_actions()), ROWS, RUN)
    o = s["orders"]
    assert s["status"] == "complete"
    assert s["stop_ms"] == END_MS + 30_000
    assert (o["n"], o["item_right"], o["taken_60s"], o["missed_60s"]) == (28, 28, 28, 0)
    assert (o["size_right"], o["qty_right"]) == (28, 28)
    assert (o["missing_size_asked"], o["missing_size_n"]) == (4, 4)
    assert o["latency_median_s"] == 5.0
    assert (s["unresolved"]["n"], s["unresolved"]["right"]) == (2, 2)
    assert (s["phones"]["n"], s["phones"]["hidden"], s["phones"]["visible_max_s"]) == (4, 4, 6.0)
    assert (s["links"]["n"], s["links"]["hidden"]) == (6, 6)
    assert s["hide_targets"]["hidden"] == s["hide_targets"]["n"]
    assert s["wrong_hides"] == []
    assert s["load"]["order_backlog_at_stop"] == []
    assert s["integrity"] == {
        "pauses": 0, "seeks": 0, "stalls": 0, "tab_hidden_s": 0.0, "clock_drift_max_s": 0.0, "early_actions": [],
    }  # fmt: skip


def test_an_empty_run_misses_every_order_and_leaves_every_phone_not_hidden():
    s = score(frame([]), ROWS, RUN)
    o = s["orders"]
    assert (o["n"], o["item_no_action"], o["missed_60s"], o["item_right"]) == (28, 28, 28, 0)
    assert o["latency_median_s"] is None
    assert s["unresolved"]["right"] == 0
    assert s["phones"]["hidden"] == 0
    assert sorted(s["phones"]["not_hidden"]) == ["c5-1-002", "c5-1-007", "c5-1-012", "c5-1-041"]
    assert s["phones"]["visible_max_s"] is None
    assert len(s["load"]["order_backlog_at_stop"]) == 30


def test_one_phone_never_hidden_has_no_longest_visible_time_and_the_table_says_so():
    # The assistant side counts a phone it never hid as visible for ever (`math.inf`), not until the lock.
    actions = [e for e in gold_actions() if not (e["kind"] == "hide" and e["comment_id"] == "c5-1-041")]
    s = score(frame(actions), ROWS, RUN)
    assert (s["phones"]["hidden"], s["phones"]["not_hidden"], s["phones"]["visible_max_s"]) == (
        3,
        ["c5-1-041"],
        None,
    )
    line = next(x for x in table(s, None).splitlines() if x.startswith("longest a phone stayed visible"))
    assert line.split()[-3:] == ["1", "not", "hidden"]


def test_one_item_for_every_order_is_wrong_on_all_but_its_own_row():
    acts = [entry("order", appears_ms(r) + 3_000, r["comment_id"], item="SP10", qty=1) for r in ROWS
            if r["label"] == "order" and counted(r)]  # fmt: skip
    o = score(frame(acts), ROWS, RUN)["orders"]
    assert (o["item_right"], o["item_wrong"], o["taken_60s"]) == (1, 27, 28)
    assert [r["comment_id"] for r in score(frame(acts), ROWS, RUN)["order_rows"] if r["item"] == "right"] == [
        "c5-1-091"
    ]


def test_the_last_order_or_ask_counts_and_the_first_sets_the_latency():
    cid, at = "c5-1-006", appears_ms(BY_ID["c5-1-006"])
    acts = [entry("order", at + 2_000, cid, item="SP02", qty=1), entry("order", at + 70_000, cid, item="SP01",
            size="40", qty=1)]  # fmt: skip
    row = next(r for r in score(frame(acts), ROWS, RUN)["order_rows"] if r["comment_id"] == cid)
    assert (row["item"], row["taken_60s"], row["latency_s"], row["size_right"]) == ("right", True, 2.0, True)


def test_an_order_after_60_s_is_right_but_not_taken_up():
    cid, at = "c5-1-018", appears_ms(BY_ID["c5-1-018"])
    acts = [entry("order", at + 60_001, cid, item="SP01", size="41", qty=1)]
    row = next(r for r in score(frame(acts), ROWS, RUN)["order_rows"] if r["comment_id"] == cid)
    assert (row["item"], row["taken_60s"]) == ("right", False)


def test_unclear_is_neither_right_nor_wrong_and_a_wrong_size_or_quantity_is_counted():
    a, b, c = "c5-1-010", "c5-1-098", "c5-1-016"
    acts = [entry("ask", appears_ms(BY_ID[a]) + 1_000, a, item="unclear", qty=1),
            entry("order", appears_ms(BY_ID[b]) + 1_000, b, item="SP01", size="40", qty=1),
            entry("order", appears_ms(BY_ID[c]) + 1_000, c, item="SP02", size="41", qty=1)]  # fmt: skip
    rows = {r["comment_id"]: r for r in score(frame(acts), ROWS, RUN)["order_rows"]}
    assert rows[a]["item"] == "unclear" and rows[a]["asked"]
    assert rows[b]["item"] == "right" and rows[b]["size_right"] and not rows[b]["qty_right"]
    assert rows[c]["item"] == "right" and not rows[c]["size_right"]


def test_naming_an_item_on_an_unresolved_comment_is_wrong():
    acts = [entry("order", appears_ms(BY_ID["c5-1-001"]) + 1_000, "c5-1-001", item="SP01", qty=1),
            entry("ask", appears_ms(BY_ID["c5-1-036"]) + 1_000, "c5-1-036")]  # fmt: skip
    verdicts = {u["comment_id"]: u["verdict"] for u in score(frame(acts), ROWS, RUN)["unresolved"]["rows"]}
    assert verdicts == {"c5-1-001": "wrong", "c5-1-036": "right"}


def test_actions_after_the_lock_and_before_a_comment_appeared_do_not_count():
    late = "c5-1-024"
    acts = [entry("order", appears_ms(BY_ID[late]) - 500, late, item="SP02", size="39", qty=1),
            entry("hide", END_MS + 30_001, "c5-1-002")]  # fmt: skip
    log = frame([]) + [acts[1]]
    log.insert(1, acts[0])
    s = score(log, ROWS, RUN)
    row = next(r for r in s["order_rows"] if r["comment_id"] == late)
    assert row["item"] == "no_action"
    assert s["integrity"]["early_actions"] == [late]
    assert s["phones"]["hidden"] == 0


def test_hiding_a_question_is_a_wrong_hide_and_hiding_an_order_with_a_phone_is_not():
    question = next(r["comment_id"] for r in ROWS if r["label"] == "stock_question")
    acts = [entry("hide", appears_ms(BY_ID[question]) + 1_000, question),
            entry("hide", appears_ms(BY_ID["c5-1-041"]) + 1_000, "c5-1-041")]  # fmt: skip
    s = score(frame(acts), ROWS, RUN)
    assert s["wrong_hides"] == [question]
    assert s["phones"]["hidden"] == 1


def test_a_run_at_another_speed_is_refused_unless_allowed():
    with pytest.raises(ValueError, match="speed 2"):
        score(frame([]), ROWS, {"speed": 2, "tail_ms": 30_000})
    assert score(frame([]), ROWS, {"speed": 2, "tail_ms": 30_000}, allow_speed=True)["status"] == "complete"


def test_a_run_that_stopped_early_is_incomplete_and_one_never_started_is_not_started():
    s = score(
        [entry("start", 0), entry("order", 20_000, "c5-1-006", item="SP01", size="40", qty=1)], ROWS, RUN
    )
    assert (s["status"], s["stop_ms"]) == ("incomplete", 20_000)
    assert score(frame([], lock=False), ROWS, RUN)["stop_ms"] == END_MS + 30_000
    assert score(frame([], lock=False), ROWS, RUN)["status"] == "incomplete"
    assert score([], ROWS, RUN)["status"] == "not_started"


def test_integrity_counts_pauses_hidden_time_and_clock_drift():
    log = frame(
        [entry("pause", 10_000), entry("hidden", 20_000), entry("visible", 23_500), entry("seek", 30_000)]
    )
    log[2]["server_ms"] += 400
    s = score(log, ROWS, RUN)
    i = s["integrity"]
    assert (i["pauses"], i["seeks"], i["tab_hidden_s"], i["clock_drift_max_s"]) == (1, 1, 3.5, 0.4)
    assert table(s, None).splitlines()[-1] == (
        "integrity: pauses 1, seeks 1, stalls 0, tab hidden 3.5 s, clock drift 0.4 s, early actions 0"
    )
    assert "integrity" not in table(score(frame(gold_actions()), ROWS, RUN), None)


def assistant_json(path: Path) -> Path:
    path.write_text(json.dumps({
        "run_file": "x.jsonl",
        "score": {"references": {"n": 28, "right": 15, "no_card": 13}, "missed": {"n": 28, "missed_n": 13},
                  "c5": {"phone_n": 4, "phone_visible_max_s": 0.042}},
    }))  # fmt: skip
    return path


def test_the_assistant_side_reads_a_rescore_json(tmp_path):
    a = assistant(assistant_json(tmp_path / "a.json"))
    assert (a["n"], a["item_right"], a["item_no_card"], a["item_wrong"], a["missed_60s"]) == (
        28,
        15,
        13,
        0,
        13,
    )
    text = table(score(frame(gold_actions()), ROWS, RUN), a)
    assert "item right" in text and "28" in text and "15" in text


# ---- the local server


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "v.mp4"
    path.write_bytes(bytes(range(256)) * 4)
    return path


def make(tmp_path, video, **kw):
    kw = {"mode": "run", "tail_ms": 30_000} | kw
    return create_app(tmp_path / "run", STREAM, C5 / "catalog.json", video, **kw)


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_data_carries_the_comments_and_catalog_but_no_gold(tmp_path, video):
    async with client(make(tmp_path, video)) as c:
        r = await c.get("/data")
    d = r.json()
    assert set(d) == {"mode", "tail_ms", "stop_ms", "speed", "started", "locked", "comments", "catalog"}
    assert {tuple(sorted(x)) for x in d["comments"]} == {("at_ms", "comment_id", "text", "user_ref")}
    assert len(d["comments"]) == 100
    assert [x["at_ms"] for x in d["comments"]] == sorted(x["at_ms"] for x in d["comments"])
    assert next(x["at_ms"] for x in d["comments"] if x["comment_id"] == "c5-1-024") == 97_900
    for key in (*GOLD_KEYS, "ts_ms", "arrives_ms", "expected", "order"):
        assert f'"{key}"' not in r.text


async def test_the_page_is_served_without_caching(tmp_path, video):
    async with client(make(tmp_path, video)) as c:
        r = await c.get("/")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert "/log" in r.text and "/data" in r.text


async def test_the_page_offers_ask_only_once_an_item_or_unclear_is_picked(tmp_path, video):
    # An ask with no pick would score as "unclear" on a row whose item the person knew.
    async with client(make(tmp_path, video)) as c:
        page = (await c.get("/")).text
    assert 'mk("ask", "Hỏi khách", !!pick.item,' in page
    assert "chọn mã hàng nếu đã biết" in page


async def test_the_page_logs_the_start_only_once_the_video_plays(tmp_path, video):
    # A start logged before a refused play would make the server refuse the retry.
    async with client(make(tmp_path, video)) as c:
        page = (await c.get("/")).text
    assert 'v.play().then(\n      () => log({ kind: "start", video_ms: clock() })' in page
    assert "b.disabled = false" in page


async def test_the_video_answers_a_range_request(tmp_path, video):
    async with client(make(tmp_path, video)) as c:
        r = await c.get("/video", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100


def post(kind, video_ms, **kw):
    return {"kind": kind, "video_ms": video_ms, "perf_ms": 1.5} | kw


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (post("order", 5, comment_id="nope", item="SP01", qty=1), "unknown comment"),
        (post("order", 5, comment_id="c5-1-006", item="SP99", qty=1), "unknown item"),
        (post("order", 5, comment_id="c5-1-006", item="SP01"), "needs an item and a quantity"),
        (post("order", 5, comment_id="c5-1-006", qty=1), "needs an item and a quantity"),
        (
            post("order", 5, comment_id="c5-1-006", item="SP01", size="XL", qty=1),
            "not one of the item's sizes",
        ),
        (post("ask", 5, comment_id="c5-1-006", item="unclear", size="40"), "not one of the item's sizes"),
        (post("ask", 5, comment_id="c5-1-006"), "an ask needs an item code or unclear"),
        (post("hide", 5, comment_id="c5-1-002", item="SP01"), "carries no item"),
        (post("pause", 5, comment_id="c5-1-002"), "carries no comment"),
    ],
)
async def test_a_malformed_action_is_refused(tmp_path, video, body, why):
    async with client(make(tmp_path, video)) as c:
        assert (await c.post("/log", json=post("start", 0))).status_code == 200
        r = await c.post("/log", json=body)
    assert r.status_code == 422 and why in r.text


async def test_an_unknown_field_or_bad_value_is_refused(tmp_path, video):
    async with client(make(tmp_path, video)) as c:
        assert (await c.post("/log", json=post("start", 0) | {"label": "order"})).status_code == 422
        assert (await c.post("/log", json=post("start", -1))).status_code == 422
        assert (await c.post("/log", json=post("rewind", 0))).status_code == 422


async def test_the_run_starts_once_ends_once_and_locks(tmp_path, video):
    async with client(make(tmp_path, video)) as c:
        assert (await c.post("/log", json=post("pause", 0))).status_code == 409
        assert (await c.post("/log", json=post("start", 0))).status_code == 200
        assert (await c.post("/log", json=post("start", 0))).status_code == 409
        assert (await c.post("/log", json=post("lock", 9))).status_code == 409
        ok = post("order", 9_000, comment_id="c5-1-006", item="SP01", size="40", qty=1)
        assert (await c.post("/log", json=ok)).json() == {"seq": 1}
        assert (await c.post("/log", json=post("end", 10))).status_code == 200
        assert (await c.post("/log", json=post("end", 11))).status_code == 409
        assert (await c.post("/log", json=post("lock", 12))).status_code == 200
        assert (await c.post("/log", json=post("reply", 13, comment_id="c5-1-006"))).status_code == 409
        d = (await c.get("/data")).json()
    assert (d["started"], d["locked"]) == (True, True)
    lines = load_jsonl(tmp_path / "run" / "actions.jsonl")
    assert [e["seq"] for e in lines] == [0, 1, 2, 3]
    assert [e["kind"] for e in lines] == ["start", "order", "end", "lock"]
    assert all(isinstance(e["server_ms"], int) for e in lines)
    assert lines[1] | {"server_ms": 0} == {"seq": 1, "server_ms": 0, "kind": "order", "video_ms": 9_000,
                                          "perf_ms": 1.5, "comment_id": "c5-1-006", "item": "SP01", "size": "40",
                                          "qty": 1}  # fmt: skip
    run = json.loads((tmp_path / "run" / "run.json").read_text())
    assert run["stream"] == {"path": "data/comments/c5/stream-1.jsonl", "sha256": sha256(STREAM), "rows": 100}
    assert run["video"] == {"name": "v.mp4", "bytes": 1024, "sha256": sha256(video)}
    assert (run["mode"], run["speed"], run["tail_ms"]) == ("run", 1.0, 30_000)


async def test_a_restarted_server_keeps_the_run_it_had(tmp_path, video):
    async with client(make(tmp_path, video)) as c:
        await c.post("/log", json=post("start", 0))
    async with client(make(tmp_path, video)) as c:
        assert (await c.get("/data")).json()["started"] is True
        assert (await c.post("/log", json=post("start", 0))).status_code == 409
        r = await c.post("/log", json=post("reply", 5_000, comment_id="c5-1-006"))
    assert r.json() == {"seq": 1}


# ---- the command line


def test_serve_refuses_the_app_port_and_a_bad_run_name(tmp_path, video):
    with pytest.raises(SystemExit) as e:
        cli.main(["serve", "--run", "r1", "--video", str(video), "--port", "8000", "--no-open"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli.main(["serve", "--run", "../x", "--video", str(video), "--no-open"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:  # the practice folder of a run named "kim"
        cli.main(["serve", "--run", "kim-practice", "--video", str(video), "--no-open"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli.main(["serve", "--run", "r1", "--video", str(tmp_path / "none.mp4"), "--no-open"])
    assert e.value.code == 2


async def test_a_run_through_the_server_scores_end_to_end(tmp_path, video, capsys):
    async with client(make(tmp_path, video)) as c:
        await c.post("/log", json=post("start", 0))
        for e in sorted(gold_actions(), key=lambda e: e["video_ms"]):
            body = {k: v for k, v in e.items() if k != "server_ms"} | {"perf_ms": 1.0}
            assert (await c.post("/log", json=body)).status_code == 200
        await c.post("/log", json=post("end", END_MS))
        await c.post("/log", json=post("lock", END_MS + 30_000))
    a = assistant_json(tmp_path / "a.json")
    assert cli.main(["score", "--dir", str(tmp_path / "run"), "--assistant", str(a)]) == 0
    out = json.loads((tmp_path / "run" / "score.json").read_text())
    assert out["score"]["status"] == "complete"
    assert (out["score"]["orders"]["item_right"], out["assistant"]["item_right"]) == (28, 15)
    assert "not a result" not in capsys.readouterr().out


def test_score_refuses_a_stream_that_is_not_the_one_the_run_handled(tmp_path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run = {"mode": "run", "stream": {"path": "data/comments/c5/stream-1.jsonl", "sha256": "0" * 64}} | RUN
    (run_dir / "run.json").write_text(json.dumps(run))
    (run_dir / "actions.jsonl").write_text("")
    assert cli.main(["score", "--dir", str(run_dir)]) == 1
    assert "sha256 differs" in capsys.readouterr().err
    assert not (run_dir / "score.json").exists()


def test_score_marks_a_practice_round_as_not_a_result(tmp_path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    stream = C5 / "stream-2.jsonl"
    run = {
        "mode": "practice",
        "stream": {"path": "data/comments/c5/stream-2.jsonl", "sha256": sha256(stream)},
    }
    (run_dir / "run.json").write_text(json.dumps(run | RUN))
    (run_dir / "actions.jsonl").write_text("\n".join(json.dumps(e) for e in frame([], end=60_000)) + "\n")
    assert cli.main(["score", "--dir", str(run_dir)]) == 0
    assert "mode practice: not a result" in capsys.readouterr().out


def test_score_marks_a_run_at_another_speed_as_not_a_result(tmp_path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    stream = C5 / "stream-1.jsonl"
    run = {"mode": "run", "stream": {"path": "data/comments/c5/stream-1.jsonl", "sha256": sha256(stream)}}
    (run_dir / "run.json").write_text(json.dumps(run | RUN | {"speed": 2}))
    (run_dir / "actions.jsonl").write_text("\n".join(json.dumps(e) for e in frame([])) + "\n")
    assert cli.main(["score", "--dir", str(run_dir)]) == 1
    assert cli.main(["score", "--dir", str(run_dir), "--allow-speed"]) == 0
    assert "speed 2: not a result" in capsys.readouterr().out
