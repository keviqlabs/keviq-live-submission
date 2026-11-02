"""Score a manual run: the person's action log against the gold of the stream they handled."""

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from measure.c5_score import MISSED_MS, counted

ON_COMMENT = ("order", "ask", "hide", "reply")
HIDE_LABELS = ("scam", "spam")


def appears_ms(row: dict) -> int:
    """When the comment shows in the feed: the stream's late delivery time, else the time it was written."""
    return row.get("arrives_ms", row["ts_ms"])


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _size(v) -> str | None:
    return None if v is None else str(v)


def _item(e: dict | None) -> str | None:
    """The item an order or ask names; "unclear" and no pick both name none."""
    item = (e or {}).get("item")
    return None if item in (None, "unclear") else item


def _visible(row: dict, by_id: dict) -> dict:
    """Seconds from appearing to the first hide; a comment never hidden has none (it stays up after the run)."""
    hide = next((e for e in by_id[row["comment_id"]] if e["kind"] == "hide"), None)
    seconds = max(0, hide["video_ms"] - appears_ms(row)) / 1000 if hide else None
    return {"comment_id": row["comment_id"], "hidden": hide is not None, "visible_s": seconds}


def _hidden_group(rows: list[dict], by_id: dict) -> dict:
    """`visible_max_s` is None when any row was never hidden, as the assistant side counts it unbounded."""
    out = [_visible(r, by_id) for r in rows]
    not_hidden = [o["comment_id"] for o in out if not o["hidden"]]
    return {
        "n": len(out),
        "hidden": sum(o["hidden"] for o in out),
        "visible_max_s": None if not out or not_hidden else max(o["visible_s"] for o in out),
        "not_hidden": not_hidden,
        "rows": out,
    }


def _integrity(log: list[dict], stop: int, speed: float) -> dict:
    """Pauses, seek attempts, stalls, time the tab was hidden, and how far the video clock moved from the server's."""
    kinds = defaultdict(int)
    hidden_ms, hidden_at = 0, None
    for e in log:
        kinds[e["kind"]] += 1
        if e["kind"] == "hidden" and hidden_at is None:
            hidden_at = e["video_ms"]
        elif e["kind"] == "visible" and hidden_at is not None:
            hidden_ms, hidden_at = hidden_ms + e["video_ms"] - hidden_at, None
    if hidden_at is not None:
        hidden_ms += max(0, stop - hidden_at)
    start = next((e for e in log if e["kind"] == "start"), None)
    drift = [
        abs((e["server_ms"] - start["server_ms"]) - (e["video_ms"] - start["video_ms"]) / speed)
        for e in log
        if start and "server_ms" in e
    ]
    return {
        "pauses": kinds["pause"],
        "seeks": kinds["seek"],
        "stalls": kinds["stall"],
        "tab_hidden_s": hidden_ms / 1000,
        "clock_drift_max_s": max(drift, default=0) / 1000,
    }


def score(log: list[dict], rows: list[dict], run: dict, allow_speed: bool = False) -> dict:
    """The run's numbers on the rows `measure/c5_score.py` uses for the assistant (see README.md, Scoring)."""
    speed = run.get("speed", 1)
    if speed != 1 and not allow_speed:
        raise ValueError(f"speed {speed}: only a run at speed 1 is scored")
    lock = next((e for e in log if e["kind"] == "lock"), None)
    end = next((e for e in log if e["kind"] == "end"), None)
    started = any(e["kind"] == "start" for e in log)
    if lock:
        stop = lock["video_ms"]
    elif end:
        stop = end["video_ms"] + run["tail_ms"]
    else:
        stop = max((e["video_ms"] for e in log), default=0)
    at = {r["comment_id"]: appears_ms(r) for r in rows}
    by_id, early = defaultdict(list), []
    for e in log:
        if e["kind"] not in ON_COMMENT or e["video_ms"] > stop:
            continue
        if e["video_ms"] < at[e["comment_id"]]:
            early.append(e["comment_id"])
            continue
        by_id[e["comment_id"]].append(e)

    order_rows, unresolved, latencies = [], [], []
    for r in rows:
        if r["label"] != "order":
            continue
        o, cid = r["order"], r["comment_id"]
        drafted = [e for e in by_id[cid] if e["kind"] in ("order", "ask")]
        first, last = (drafted[0], drafted[-1]) if drafted else (None, None)
        if counted(r):
            latency = (first["video_ms"] - at[cid]) if first else None
            if latency is not None:
                latencies.append(latency / 1000)
            if last is None:
                item = "no_action"
            elif _item(last) is None:
                item = "unclear"
            else:
                item = "right" if _item(last) == o["product"] else "wrong"
            right = item == "right"
            order_rows.append({
                "comment_id": cid,
                "taken_60s": latency is not None and latency <= MISSED_MS,
                "latency_s": None if latency is None else latency / 1000,
                "item": item,
                "size_right": right and _size(last.get("size")) == _size(o.get("size")),
                "qty_right": right and last.get("qty") == o.get("qty"),
                "asked": last is not None and last["kind"] == "ask",
                "expected_action": r["expected_action"],
                "last": last and {k: last.get(k) for k in ("kind", "item", "size", "qty", "video_ms")},
            })  # fmt: skip
        elif not o.get("product"):
            verdict = "no_action" if last is None else ("right" if _item(last) is None else "wrong")
            unresolved.append({"comment_id": cid, "verdict": verdict})

    def n(key, value=True):
        return sum(1 for r in order_rows if r[key] == value)

    hides = [e for es in by_id.values() for e in es if e["kind"] == "hide"]
    targets = {
        r["comment_id"] for r in rows if r["label"] in HIDE_LABELS or r.get("has_phone") or r.get("has_link")
    }
    acts = [e for es in by_id.values() for e in es]
    minutes = stop / 60_000
    backlog = [
        r["comment_id"]
        for r in rows
        if r["label"] == "order"
        and at[r["comment_id"]] <= stop
        and not any(e["kind"] in ("order", "ask") for e in by_id[r["comment_id"]])
    ]
    missing_size = [r for r in order_rows if r["expected_action"] == "ask_customer"]
    return {
        "status": "complete" if lock else ("incomplete" if started else "not_started"),
        "stop_ms": stop,
        "session_minutes": round(minutes, 2),
        "orders": {
            "n": len(order_rows),
            "taken_60s": n("taken_60s"),
            "missed_60s": len(order_rows) - n("taken_60s"),
            "item_right": n("item", "right"),
            "item_wrong": n("item", "wrong"),
            "item_unclear": n("item", "unclear"),
            "item_no_action": n("item", "no_action"),
            "size_right": n("size_right"),
            "qty_right": n("qty_right"),
            "missing_size_asked": sum(r["asked"] for r in missing_size),
            "missing_size_n": len(missing_size),
            "latency_median_s": statistics.median(latencies) if latencies else None,
        },
        "unresolved": {
            "n": len(unresolved),
            "right": sum(u["verdict"] == "right" for u in unresolved),
            "rows": unresolved,
        },
        "phones": _hidden_group([r for r in rows if r.get("has_phone")], by_id),
        "links": _hidden_group([r for r in rows if r.get("has_link")], by_id),
        "hide_targets": {"n": len(targets), "hidden": len({e["comment_id"] for e in hides} & targets)},
        "wrong_hides": sorted({e["comment_id"] for e in hides} - targets),
        "load": {
            "actions": len(acts),
            "actions_per_minute": round(len(acts) / minutes, 2) if minutes else None,
            "replies": sum(e["kind"] == "reply" for e in acts),
            "order_backlog_at_stop": backlog,
        },
        "integrity": _integrity(log, stop, speed) | {"early_actions": early},
        "order_rows": order_rows,
    }


def assistant(path: Path) -> dict:
    """The assistant's numbers on the same stream, from a `measure/c5_rescore.py` score JSON."""
    d = json.loads(path.read_text())
    s, run_file = d["score"], d.get("run_file")
    ref, missed, c5 = s["references"], s["missed"], s["c5"]
    return {
        "source": path.name,
        "session": run_file.get("session_id") if isinstance(run_file, dict) else run_file,
        "n": ref["n"],
        "item_right": ref["right"],
        "item_no_card": ref["no_card"],
        "item_wrong": ref["n"] - ref["right"] - ref["no_card"],
        "missed_n": missed["n"],
        "missed_60s": missed["missed_n"],
        "phones": c5["phone_n"],
        "phone_visible_max_s": c5["phone_visible_max_s"],
    }


def table(mine: dict, theirs: dict | None) -> str:
    """The side-by-side lines printed after scoring."""
    o, p = mine["orders"], mine["phones"]
    if p["not_hidden"]:
        phone_max = f"{len(p['not_hidden'])} not hidden"
    else:
        phone_max = "-" if p["visible_max_s"] is None else f"{p['visible_max_s']:.1f}"
    their_max = theirs and theirs["phone_visible_max_s"]
    lines = [
        ("order comments that name an item", str(o["n"]), theirs and str(theirs["n"])),
        ("item right", str(o["item_right"]), theirs and str(theirs["item_right"])),
        ("item wrong", str(o["item_wrong"]), theirs and str(theirs["item_wrong"])),
        (
            "no item named (unclear + no action / no card)",
            str(o["item_unclear"] + o["item_no_action"]),
            theirs and str(theirs["item_no_card"]),
        ),
        ("  of which no action", str(o["item_no_action"]), theirs and "-"),
        ("not taken up within 60 s", str(o["missed_60s"]), theirs and str(theirs["missed_60s"])),
        ("phones hidden", f"{p['hidden']}/{p['n']}", theirs and f"{theirs['phones']} rows"),
        (
            "longest a phone stayed visible, s",
            phone_max,
            theirs and ("not hidden" if their_max == math.inf else str(their_max)),
        ),
        ("links hidden", f"{mine['links']['hidden']}/{mine['links']['n']}", theirs and "-"),
        ("wrong hides", str(len(mine["wrong_hides"])), theirs and "-"),
    ]
    head = f"{'':46} {'person':>10}" + (f" {'assistant':>10}" if theirs else "")
    body = [f"{k:46} {a:>10}" + (f" {b:>10}" if theirs else "") for k, a, b in lines]
    i = mine["integrity"]
    keys = ("pauses", "seeks", "stalls", "tab_hidden_s", "clock_drift_max_s", "early_actions")
    if any(i[k] for k in keys):
        body.append(
            f"integrity: pauses {i['pauses']}, seeks {i['seeks']}, stalls {i['stalls']}, "
            f"tab hidden {i['tab_hidden_s']:.1f} s, clock drift {i['clock_drift_max_s']:.1f} s, "
            f"early actions {len(i['early_actions'])}"
        )
    return "\n".join([f"status {mine['status']}, {mine['session_minutes']} min", head, *body])
