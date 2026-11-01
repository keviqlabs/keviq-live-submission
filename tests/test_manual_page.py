"""The manual page itself, run in Node (tests/manual_page.mjs) against a real local server.

An action shows as done only once the server has stored it, not while its line is in flight. A line the server
did not store stops the run there, whatever the cause (no answer, a refusal, any other status) and whichever line
it was (an action, the start, a line in the 30 seconds after the video). The page then sends nothing more: not a
line already queued behind it, not when the connection comes back before the end, not when the tab is left and
shown again. So the log has no lock and the run scores incomplete; a start that was not stored leaves no run at
all. On the healthy path every action is stored once, in click order, with the times of the click, also when it
was clicked while another line was in flight.
"""

import json
import shutil
import socket
import subprocess
import threading
import time

import pytest
import uvicorn

from app.settings import ROOT
from measure.manual.score import load_jsonl, score
from measure.manual.server import create_app

C5 = ROOT / "data" / "comments" / "c5"
STREAM = C5 / "stream-1.jsonl"
ROWS = load_jsonl(STREAM)
DRIVER = ROOT / "tests" / "manual_page.mjs"
ACTIONS = {"order", "ask", "hide", "reply"}
AFTER_ORDER = "Đơn 1 · Hỏi 0 · Ẩn 0 · Trả lời 0"


@pytest.fixture
def server(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(bytes(range(256)) * 4)
    run_dir = tmp_path / "run"
    app = create_app(run_dir, STREAM, C5 / "catalog.json", video, mode="run", tail_ms=30_000)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    srv = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=srv.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert time.monotonic() < deadline, "the local server did not start"
        time.sleep(0.02)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}", run_dir
    srv.should_exit = True
    thread.join(10)
    sock.close()


def drive(base: str, scenario: str) -> dict:
    node = shutil.which("node")
    assert node, "node is needed to run the page script"
    out = subprocess.run(
        [node, str(DRIVER), base, scenario], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def stored(run_dir):
    log = load_jsonl(run_dir / "actions.jsonl")
    return log, score(log, ROWS, json.loads((run_dir / "run.json").read_text()))


def why(mode: str) -> str:
    """The reason the page gives for a line lost as `mode`."""
    if mode == "down":
        return "mất kết nối tới server cục bộ"
    return "server trả " + ("422" if mode == "refused" else mode)


def test_a_healthy_run_stores_each_action_once_in_click_order_with_the_click_times(server):
    base, run_dir = server
    page = drive(base, "healthy")
    log, result = stored(run_dir)
    assert result["status"] == "complete"
    kinds = ["start", "order", "pause", "ask", "hide", "reply", "end", "lock"]
    assert [e["kind"] for e in log] == kinds
    assert [e["seq"] for e in log] == list(range(8))
    # The ask was clicked while the pause line was in flight and sent 300 ms later; it keeps the click's times.
    acts = [e for e in log if e["kind"] in ACTIONS]
    assert [(e["kind"], e["comment_id"], e["video_ms"], e["perf_ms"]) for e in acts] == [
        (c["kind"], c["comment_id"], c["video_ms"], c["perf_ms"]) for c in page["clicks"]
    ]
    assert (acts[0]["item"], acts[0]["size"], acts[0]["qty"]) == ("SP01", "40", 2)
    assert acts[1]["item"] == "SP02" and "size" not in acts[1]
    assert [x["kind"] for x in page["sent"]] == [e["kind"] for e in log]
    assert page["counts"] == "Đơn 1 · Hỏi 1 · Ẩn 1 · Trả lời 1"
    # The reply held in flight was not shown as done yet; once stored it was.
    reply = page["comments"][3]
    held, done = page["in_flight"], page["after_store"]
    assert (held["counts"], held["flash"], held["tags"][reply]) == (
        "Đơn 1 · Hỏi 1 · Ẩn 1 · Trả lời 0",
        "",
        [],
    )
    assert (done["counts"], done["tags"][reply]) == ("Đơn 1 · Hỏi 1 · Ẩn 1 · Trả lời 1", ["Trả lời"])
    assert done["flash"] == "Đã trả lời · 00:43"
    assert "trang đã khoá" in page["sheet"]
    assert "Đã ghi: 1 đơn, 1 câu hỏi, 1 lần ẩn, 1 trả lời" in page["sheet"]
    assert page["unhandled"] == []


@pytest.mark.parametrize("scenario", ["down", "refused"])
def test_a_line_the_server_did_not_store_stops_the_run_though_the_connection_comes_back(server, scenario):
    base, run_dir = server
    page = drive(base, scenario)
    log, result = stored(run_dir)
    asked = page["comments"][1]
    assert result["status"] == "incomplete"
    assert [e["kind"] for e in log] == ["start", "order"]
    # The ask was the last line sent: no reply, end or lock after it, though the server answered again.
    assert [(x["kind"], x["via"]) for x in page["sent"]] == [
        ("start", "ok"),
        ("order", "ok"),
        ("ask", scenario),
    ]
    if scenario == "refused":
        assert page["sent"][-1]["status"] == 422
    # The page never showed the lost ask as done, and stopped the video.
    loss = page["after_loss"]
    assert (loss["running"], loss["paused"]) == (False, True)
    assert loss["counts"] == AFTER_ORDER
    assert "Đã hỏi khách" not in loss["flash"]
    assert loss["tags"][asked] == []
    assert loss["tally"] == {"order": 1, "ask": 0, "hide": 0, "reply": 0}
    assert "Đã ghi: 1 đơn, 0 câu hỏi" in loss["sheet"] and "chưa xong" in loss["sheet"]
    assert f"ask lúc 00:41 ({why(scenario)})" in loss["sheet"]
    # Leaving the tab and coming back after the loss did not resume the run either.
    assert (page["running"], page["counts"], page["sheet"]) == (False, loss["counts"], loss["sheet"])
    assert page["unhandled"] == []


@pytest.mark.parametrize("scenario", ["queued-down", "queued-refused"])
def test_a_line_in_flight_is_not_shown_as_done_and_nothing_queued_behind_a_lost_line_is_sent(
    server, scenario
):
    base, run_dir = server
    page = drive(base, scenario)
    log, result = stored(run_dir)
    asked = page["comments"][1]
    held = page["in_flight"]
    assert (held["counts"], held["flash"], held["tags"][asked], held["running"]) == (
        AFTER_ORDER,
        "",
        [],
        True,
    )
    assert page["second_click"] is False
    # A pause and a tab switch were logged while the ask was in flight; none was sent, though the server answered
    # again, and nothing was sent when the tab was left and shown again after the loss.
    assert page["playing_when_paused"] is True
    assert [(x["kind"], x["via"]) for x in page["sent"]] == [
        ("start", "ok"),
        ("order", "ok"),
        ("ask", scenario.removeprefix("queued-")),
    ]
    assert [e["kind"] for e in log] == ["start", "order"]
    assert result["status"] == "incomplete"
    loss = page["after_loss"]
    assert (loss["counts"], loss["tags"][asked]) == (held["counts"], [])
    assert (loss["running"], loss["paused"]) == (False, True)
    assert page["running"] is False and "chưa xong" in page["sheet"]
    assert page["unhandled"] == []


@pytest.mark.parametrize(
    ("kind", "mode"),
    [
        ("order", "down"),
        ("order", "409"),
        ("ask", "500"),
        ("hide", "409"),
        ("hide", "500"),
        ("reply", "500"),
        ("reply", "refused"),
    ],
)
def test_no_action_lost_in_flight_is_shown_as_done_whatever_the_server_answered(server, kind, mode):
    base, run_dir = server
    page = drive(base, f"held-{kind}-{mode}")
    log, result = stored(run_dir)
    target = page["comments"][1]
    assert page["in_flight"]["running"] is True
    for shown in (page["in_flight"], page["after_loss"]):
        assert (shown["counts"], shown["flash"], shown["tags"][target]) == (AFTER_ORDER, "", [])
    loss = page["after_loss"]
    assert (loss["running"], loss["paused"]) == (False, True)
    assert loss["tally"] == {"order": 1, "ask": 0, "hide": 0, "reply": 0}
    assert f"{kind} lúc 00:41 ({why(mode)})" in loss["sheet"] and "chưa xong" in loss["sheet"]
    # Sent once, not retried, and nothing after it.
    assert [(x["kind"], x["via"]) for x in page["sent"]] == [("start", "ok"), ("order", "ok"), (kind, mode)]
    if mode != "down":
        assert page["sent"][-1]["status"] == (422 if mode == "refused" else int(mode))
    assert [e["kind"] for e in log] == ["start", "order"]
    assert result["status"] == "incomplete"
    assert (page["running"], page["sheet"]) == (False, loss["sheet"])
    assert page["unhandled"] == []


@pytest.mark.parametrize("mode", ["down", "refused", "500"])
def test_a_start_the_server_did_not_store_starts_no_run(server, mode):
    base, run_dir = server
    page = drive(base, f"start-{mode}")
    assert [(x["kind"], x["via"]) for x in page["sent"]] == [("start", mode)]
    assert sorted(run_dir.iterdir()) == []  # no run.json and no log: nothing to score
    assert (page["running"], page["paused"], page["overlay"]) == (False, True, "flex")
    assert page["sheet"].startswith("Không bắt đầu được") and why(mode) in page["sheet"]
    assert page["counts"] == ""
    assert page["unhandled"] == []


@pytest.mark.parametrize("mode", ["down", "409"])
def test_a_line_lost_after_the_video_ended_stops_the_run_before_the_page_locks(server, mode):
    base, run_dir = server
    page = drive(base, f"tail-{mode}")
    log, result = stored(run_dir)
    assert [(x["kind"], x["via"]) for x in page["sent"]] == [
        ("start", "ok"),
        ("order", "ok"),
        ("end", "ok"),
        ("reply", mode),
    ]
    assert [e["kind"] for e in log] == ["start", "order", "end"]
    assert result["status"] == "incomplete"
    # The 30 seconds ran out after the loss: the page neither locked nor waited on a lock it never sent.
    sheet = page["sheet"]
    assert "chưa xong" in sheet and "đang lưu" not in sheet and "trang đã khoá" not in sheet
    assert sheet == page["after_loss"]["sheet"]
    assert (page["running"], page["locked"], page["paused"]) == (False, False, True)
    assert page["unhandled"] == []


def test_a_lost_lock_line_is_not_shown_as_a_finished_run(server):
    base, run_dir = server
    page = drive(base, "lock-down")
    log, result = stored(run_dir)
    assert result["status"] == "incomplete"
    assert [e["kind"] for e in log] == ["start", "order", "end"]
    waiting = page["lock_in_flight"]["sheet"]
    assert "đang lưu" in waiting and "trang đã khoá" not in waiting
    assert "trang đã khoá" not in page["sheet"] and "chưa xong" in page["sheet"]
    assert page["unhandled"] == []
