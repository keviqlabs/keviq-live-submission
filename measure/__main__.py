"""python -m measure --session <id> [--gold FILE] | --events-file FILE [--no-write]

Prints the measurement table (C1, C3, C5, C8, cost). JSON goes to docs/evidence/<VN date>/ unless --no-write.
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from app.settings import ROOT, database_url
from measure.core import VN, report


async def from_store(session: str) -> list[dict]:
    from app.store import make_store

    store = make_store(database_url())
    await store.open()
    try:
        events, cursor = [], 0
        while rows := await store.read(session, cursor, 1000):
            events += rows
            cursor = rows[-1]["seq"]
        return events
    finally:
        await store.close()


def fmt(v, digits=3):
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}" if isinstance(v, float) else str(v)


def table(r: dict) -> str:
    lines = [
        f"events: {r['events']}  (hand-written fx- fixtures: {r['fixtures']}; numbers over fixtures are not model results)"
    ]
    c1 = r["c1"]
    lines.append(
        f"C1 consequence within {c1['cycle_ms']} ms: {c1['covered']}/{c1['n']}  missing={c1['missing'][:5]}"
    )
    if "c3" in r:
        c3, c5, lab = r["c3"], r["c5"], r["labels"]
        lines.append(
            f"C3 referenced_card_id accuracy: {fmt(c3['accuracy'])} on n={c3['n']} (target >= 0.9 on 50)"
        )
        lines.append(f"C5 order recall: {fmt(c5['order_recall'])} on n={c5['order_n']} (target >= 0.9)")
        lines.append(
            f"C5 seller interventions: {c5['interventions']} in {c5['session_minutes']} min"
            f" = {fmt(c5['interventions_per_hour'], 1)}/h (target <= 10/h)"
        )
        lines.append(
            f"C5 phone visible max: {fmt(c5['phone_visible_max_s'], 1)} s on n={c5['phone_n']} (target <= 5 s)"
        )
        lines.append(
            f"C5 receipts with image and version price: {c5['receipts_ok']}/{c5['receipts']} (target 100%)"
        )
        lines.append(f"labels micro-F1: {fmt(lab['micro_f1'])} ({lab['labeled']}/{lab['n']} labeled)")
    lines.append("C8 latency ms (p50 / p95, n):")
    if not r["c8_latency"]:
        lines.append("  no events with timing yet (fixtures carry none; worker output does)")
    for etype, stages in sorted(r["c8_latency"].items()):
        cells = [f"{s}={fmt(v['p50'])}/{fmt(v['p95'])} n={v['n']}" for s, v in stages.items()]
        lines.append(f"  {etype}: " + "  ".join(cells))
    lines.append("cost by day and model:")
    lines += [
        f"  {c['day']} {c['model']}: {c['calls']} calls, {c['errors']} errors, ${c['cost_usd']:.4f}"
        for c in r["cost"]
    ]
    if not r["cost"]:
        lines.append("  no model_call events")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(prog="measure")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--session")
    src.add_argument("--events-file")
    p.add_argument("--gold")
    p.add_argument("--no-write", action="store_true")
    args = p.parse_args(argv)
    if args.events_file:
        events = [json.loads(x) for x in Path(args.events_file).read_text().splitlines() if x.strip()]
    else:
        events = asyncio.run(from_store(args.session))
    gold = json.loads(Path(args.gold).read_text()) if args.gold else None
    r = report(events, gold)
    print(table(r))
    if not args.no_write:
        now = datetime.now(VN)
        name = args.session or Path(args.events_file).parent.name
        out = ROOT / "docs" / "evidence" / now.date().isoformat() / f"measure-{name}-{now:%H%M%S}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(r, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
