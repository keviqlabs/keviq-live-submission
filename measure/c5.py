"""C5 runner: test1 x5 in one session on the real clock, scored by measure/c5_score and measure/c5_report.

  uv run python -m measure.c5 --dry [--loops 5] [--out DIR]
      0 USD: in-process app on keviq_test, MODEL_API_KEY empty. The 6b vision rows and the test1 lines stand in
      for media; the labels brain runs with the gold labels in place of the model (no model_call). measure/c5_dry
  uv run python -m measure.c5 --base-url URL [--loops 5] [--out DIR] [--seconds N] [--paid]
      a running app: per loop the worker (--session) and media start under measure/c5_proc and stop after the
      loop; without --paid their MODEL_API_KEY is empty (0 USD, plumbing only). measure/c5_live

Per loop k, on the session clock from the loop's t0: the comments of stream-k at t0 + ts_ms (posted at
t0 + arrives_ms when one arrives late), the shop and the customers of measure/c5_shop; at the loop end every
open card is closed by posting a retired version (never deleted), the `Session.waits` snapshot is kept and the
worker state goes (a restart). Writes <run>.json, <run>-scenarios.json and <run>-comments.jsonl to --out,
without comment text, utterances or phone numbers, and prints the SQL ledger of the session.
"""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
C5 = ROOT / "data/comments/c5"
LABELS = ROOT / "data/local/test1/labels.json"  # local gold: product prices (not in git)
TAIL_MS = 30_000  # after the last frame: late comments, customers and the shop finish
SQL = """SELECT session_id, count(*) AS n, COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd
FROM events WHERE type = 'model_call' AND session_id = %s GROUP BY session_id;"""


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def streams(k: int) -> tuple[list[dict], list[dict]]:
    return read_jsonl(C5 / f"stream-{k}.jsonl"), read_jsonl(C5 / f"messenger-{k}.jsonl")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_path(path: Path) -> str:
    """The path as the evidence records it: relative to the checkout when inside it (no private prefix)."""
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()


def comment_event(row: dict, sid: str, t0: int) -> dict:
    from app.contracts import make_event

    return make_event("comment", sid, t0 + row["ts_ms"], "sim", event_id=f"{sid}.{row['comment_id']}",
                      comment_id=row["comment_id"], user_ref=row["user_ref"], text=row["text"], reply_to=None)  # fmt: skip


def comment_schedule(rows: list[dict], sid: str, t0: int) -> list[tuple[int, dict]]:
    """(post at, comment event): written at t0 + ts_ms, posted when it arrives."""
    return [(t0 + r.get("arrives_ms", r["ts_ms"]), comment_event(r, sid, t0)) for r in rows]


def retire_events(feed, sid: str) -> list[dict]:
    """A retired version on top of every card of the session that is still open: the card closes, nothing is
    deleted, and the next loop's comments cannot pin to it."""
    from app.contracts import make_event, now_ms
    from worker.brain.card import CARD_FIELDS

    out = []
    for card_id in feed.cards:
        last = feed.latest(card_id)
        if last["status"] == "retired":
            continue
        fields = {f: last[f] for f in CARD_FIELDS if f not in ("version", "status")}
        v = last["version"] + 1
        out.append(make_event("card", sid, now_ms(), last["source"], event_id=f"{sid}.retire-{card_id}-v{v}",
                              caused_by=[last["event_id"]], version=v, status="retired", **fields))  # fmt: skip
    return out


async def drain(*task_sets: set, timeout_s: float = 60.0) -> int:
    """Wait for the tasks in these sets (they may add more while they run); returns how many were still running
    at the timeout."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        pending = [t for s in task_sets for t in s if not t.done()]
        if not pending:
            return 0
        await asyncio.wait(pending, timeout=max(end - time.monotonic(), 0.01))
    return sum(1 for s in task_sets for t in s if not t.done())


def code_checks() -> tuple[dict, dict]:
    """The "mã đã kiểm" column: each situation's test files, run on their own (rc per file, worst rc per situation)."""
    from measure.c5_score import SITUATION_TESTS

    env = {**os.environ, "MODEL_API_KEY": "", "CATALOG_PATH": ""}
    env.pop("DATABASE_URL", None)
    by_file = {}
    for f in sorted({f for fs in SITUATION_TESTS.values() for f in fs}):
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f], cwd=ROOT, env=env, capture_output=True, text=True
        )
        by_file[f] = {"rc": r.returncode, "tail": (r.stdout.strip().splitlines() or [""])[-1]}
    return {n: max(by_file[f]["rc"] for f in fs) for n, fs in SITUATION_TESTS.items()}, by_file


def ledger(url: str, sid: str) -> dict:
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        rows = conn.execute(SQL, (sid,)).fetchall()
    return {"query": SQL.replace("%s", f"'{sid}'"), "rows": [[r[0], r[1], str(r[2])] for r in rows],
            "rowcount": len(rows)}  # fmt: skip


def comment_lines(events: list[dict], run_file: dict, rows_by_loop: dict, gold) -> list[dict]:
    """One row per posted comment: what the system did with it, by id only (no text)."""
    from measure import core
    from measure.c5_score import MISSED_MS, Run
    from orders.flow import order_id_for

    run = Run(events, run_file["loops"], gold)
    out = []
    for k, rows in rows_by_loop.items():
        for r in rows:
            c = run.comments.get(r["comment_id"])
            if c is None:
                continue
            oid = order_id_for(r["comment_id"])
            card, source = run.card_of(r["comment_id"])
            label = run.labels.get(r["comment_id"]) or {}
            first = run.first_reply_ms(r["comment_id"])
            delay = first - core.when(c) if first is not None else None
            out.append({
                "loop": k, "comment_id": r["comment_id"], "gold_label": r["label"],
                "expected_action": r.get("expected_action"), "situations": r.get("situations", []),
                "label": label.get("label"), "action": label.get("action"),
                "order_id": oid if run.orders.get(oid) else None,
                "states": [o["state"] for o in run.orders.get(oid, [])],
                "messages": [m["template"] for m in run.out.get(oid, [])],
                "label_card": run.label_card(r["comment_id"]), "card_id": card, "card_source": source,
                "card_item": run.item.get(card),
                "gold_item": (r.get("order") or {}).get("product"), "first_reply_ms": delay,
                "missed": r["label"] == "order" and bool((r.get("order") or {}).get("product"))
                and (delay is None or delay > MISSED_MS),
            })  # fmt: skip
    return out


def write_evidence(out_dir: Path, run_file: dict, scored: dict, lines: list[dict]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = run_file["run"]
    paths = [out_dir / f"{name}.json", out_dir / f"{name}-scenarios.json", out_dir / f"{name}-comments.jsonl"]
    paths[0].write_text(
        json.dumps({"run_file": run_file, "score": scored}, ensure_ascii=False, indent=1) + "\n"
    )
    paths[1].write_text(json.dumps(scored["situations"], ensure_ascii=False, indent=1) + "\n")
    paths[2].write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
    return paths


def finish(events: list[dict], run_file: dict, rows_by_loop: dict, msgs_by_loop: dict, out_dir: Path,
           db_url: str, check_code: bool) -> dict:  # fmt: skip
    """Score the run, write the evidence files and print the ledger."""
    from measure.c5_report import score
    from measure.c5_shop import Gold

    gold = Gold.load(C5, LABELS)
    catalog = json.loads((ROOT / run_file["config"]["catalog_path"]).read_text())
    code_rc = None
    if check_code:
        code_rc, run_file["code_checks"] = code_checks()
    rows = [r for k in sorted(rows_by_loop) for r in rows_by_loop[k]]
    msgs = [m for k in sorted(msgs_by_loop) for m in msgs_by_loop[k]]
    scored = score(events, run_file, rows, msgs, gold, catalog, code_rc)
    run_file["ledger"] = ledger(db_url, run_file["session_id"])
    paths = write_evidence(out_dir, run_file, scored, comment_lines(events, run_file, rows_by_loop, gold))
    print("run", run_file["run"], "sha", run_file["sha"], "loops", len(run_file["loops"]))
    for p in paths:
        print("wrote", p.relative_to(ROOT) if p.is_relative_to(ROOT) else p)
    print(run_file["ledger"]["query"])
    print("session_id | n | usd")
    for row in run_file["ledger"]["rows"]:
        print(" | ".join(str(x) for x in row))
    print(f"({run_file['ledger']['rowcount']} rows)")
    return scored


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="measure.c5")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry", action="store_true", help="0 USD, in-process on keviq_test")
    mode.add_argument("--base-url", help="a running app (worker and media are started per loop)")
    p.add_argument("--loops", type=int, default=5)
    p.add_argument(
        "--run", help="run id = session id (default c5-test1x5-MMDD-HHMMSS, c5-dry-... with --dry)"
    )
    p.add_argument("--catalog", default=str(C5 / "catalog.json"))
    p.add_argument("--out", default=str(ROOT / ".scratch/c5"))
    p.add_argument("--no-code-check", action="store_true", help="skip the per-situation test files")
    p.add_argument(
        "--seconds", type=float, help="live: only the first N seconds of each loop (plumbing test)"
    )
    p.add_argument("--paid", action="store_true", help="live: the children keep MODEL_API_KEY (paid calls)")
    a = p.parse_args(argv)
    if not 1 <= a.loops <= 5:
        p.error("--loops is 1..5 (stream-1..5)")
    if a.dry and (a.seconds or a.paid):
        p.error("--seconds and --paid are for a live run")
    run_id = a.run or f"c5-{'dry' if a.dry else 'test1x5'}-{time.strftime('%m%d-%H%M%S')}"
    args = {"run": run_id, "loops": a.loops, "catalog": Path(a.catalog).resolve(), "out": Path(a.out),
            "check_code": not a.no_code_check}  # fmt: skip
    if a.dry:
        from measure import c5_dry

        return asyncio.run(c5_dry.main(**args))
    from measure import c5_live

    return asyncio.run(c5_live.main(base_url=a.base_url, seconds=a.seconds, paid=a.paid, **args))


if __name__ == "__main__":
    sys.exit(main())
