"""C5 rescore: score a stored dry session again with the scorer at HEAD, without running anything.

  uv run python -m measure.c5 --rescore RUN_JSON [--out DIR]

RUN_JSON is a run's <run>.json, or a committed <run>-rescore.json; the second keeps its name, so the new files can
be compared byte for byte with the committed ones. The events come from keviq_test, where the dry run wrote them
(they hold comment text, so they are not in git): while the session is there, the same scorer gives the same
files. The ledger is read again; the code column stays NOT_RUN (no test files run)."""

import asyncio
import json
from pathlib import Path

from measure import c5


def rescore_run_file(run_file: dict, src_name: str) -> dict:
    """The run file the rescore writes: `<run>-rescore` with `rescored_from`, and the catalog path relative to the
    checkout. A run file that is already a rescore keeps its name."""
    out = dict(run_file)
    if "rescored_from" not in out:
        out |= {"run": out["run"] + "-rescore", "rescored_from": src_name}
    out["config"] = {**out["config"], "catalog_path": c5.repo_path(c5.ROOT / out["config"]["catalog_path"])}
    return out


async def read_session(url: str, sid: str) -> list[dict]:
    from app.store import PgStore
    from measure.c5_dry import read_all

    store = PgStore(url)
    await store.open()
    try:
        return await read_all(store, sid)
    finally:
        await store.close()


def main(src: Path, out: Path) -> int:
    from app import settings
    from measure.c5_report import score
    from measure.c5_shop import Gold

    run_file = rescore_run_file(json.loads(src.read_text())["run_file"], src.name)
    settings.load_dotenv(c5.ROOT / ".env")
    url = settings.testing_database_url()
    if not settings.is_test_database(url):
        raise SystemExit("refusing: not a _test database")
    events = asyncio.run(read_session(url, run_file["session_id"]))
    if not events:
        raise SystemExit(f"no events for {run_file['session_id']} in keviq_test")
    rows_by_loop, msgs_by_loop = {}, {}
    for k in (lp["k"] for lp in run_file["loops"]):
        rows_by_loop[k], msgs_by_loop[k] = c5.streams(k)
    rows = [r for k in sorted(rows_by_loop) for r in rows_by_loop[k]]
    msgs = [m for k in sorted(msgs_by_loop) for m in msgs_by_loop[k]]
    gold = Gold.load(c5.C5, c5.LABELS)
    catalog = json.loads((c5.ROOT / run_file["config"]["catalog_path"]).read_text())
    scored = score(events, run_file, rows, msgs, gold, catalog, None)
    run_file["ledger"] = c5.ledger(url, run_file["session_id"])
    lines = c5.comment_lines(events, run_file, rows_by_loop, gold)
    paths = c5.write_evidence(out, run_file, scored, lines)
    print("rescore", run_file["run"], "session", run_file["session_id"], "events", len(events))
    for p in paths:
        print("wrote", p.name, c5.sha256(p))
    print("ledger rows", run_file["ledger"]["rowcount"])
    return 0
