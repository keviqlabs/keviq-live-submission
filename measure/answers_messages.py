"""Offline score of the private replies of answers/message.py: python -m measure.answers_messages [--rescore]

Same eval set (data/answers/eval-c5.jsonl) and same independent judge as measure/answers_eval.py, but through the
path that runs: each comment labelled price_question or stock_question goes through on_question() over the
session events stored by the time the reply is built (DELAY_MS after the comment), and the reply, if any, is
judged at that moment. No grouping and no cooldown: every commenter gets their own private reply. Comments with
other labels get nothing from this path (an order without a size is the order flow's ask_variant), so the
ask_size group shows here as no answer. --rescore runs the size and stock cases of the 31 October paid rescore
(loop 1) the same way; for stock this is what C5 counts: a message_out that carries the question's comment_id.
"""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from answers.message import on_question
from answers.selector import Decision
from connectors.sim.messenger import SimMessenger
from measure.answers_eval import Truth, judge, load, rescore, table, wrong_facts

DELAY_MS = 2000
SWEEP_MS = (0, 1000, 2000, 3000, 5000)
SID = "answers-eval"


def session_events(rows: list[dict], upto_ms: int) -> list[dict]:
    """The events a session would hold at upto_ms: card versions, comments and their labels."""
    out = []
    for r in rows:
        if r["t_ms"] > upto_ms:
            continue
        head = {"session_id": SID, "ts_ms": r["t_ms"], "source": "replay", "v": 1}
        if r["type"] == "card":
            p = r["payload"]
            out.append({**p, **head, "type": "card", "event_id": f"card.{p['card_id']}.v{p['version']}"})
        elif r["type"] == "comment":
            cid = r["comment_id"]
            out.append({**head, "type": "comment", "event_id": f"cm.{cid}", "comment_id": cid,
                        "user_ref": f"u-{cid}", "text": r["text"], "reply_to": None})  # fmt: skip
            out.append({**head, "type": "comment_labeled", "event_id": f"lb.{cid}", "comment_id": cid,
                        "label": r.get("label")})  # fmt: skip
    return out


async def _replies(rows: list[dict], delay_ms: int = DELAY_MS) -> list[dict]:
    truth = Truth(rows)
    out = []
    for r in rows:
        if r["type"] != "comment":
            continue
        cid, now = r["comment_id"], r["t_ms"] + delay_ms
        events = session_events(rows, now)
        label = next(e for e in events if e["type"] == "comment_labeled" and e["comment_id"] == cid)
        partials, reason = await on_question(label, events, SimMessenger(), now)
        if partials:
            p = partials[0]["payload"]
            d = Decision(now, (cid,), p["kind"], None, reason, p["text"], p["card_id"], p["card_version"], reason,
                         r["t_ms"])  # fmt: skip
        else:
            d = Decision(now, (cid,), "", None, "silent", "", None, None, reason, r["t_ms"])
        outcome, detail = judge(r["gold"], d, truth, r["t_ms"])
        out.append({"comment_id": cid, "category": r["gold"]["category"], "outcome": outcome, "detail": detail,
                    "said": d.text, "reason": reason, "replied": bool(partials)})  # fmt: skip
    return out


def run_messages(rows: list[dict], labels: bool = True, delay_ms: int = DELAY_MS) -> tuple[None, list[dict]]:
    """One result row per comment, in the shape of measure.answers_eval.run_loop (labels are always used)."""
    return None, asyncio.run(_replies(rows, delay_ms))


def score(loops: dict[int, list[dict]], delay_ms: int = DELAY_MS) -> dict:
    results = [row for rows in loops.values() for row in run_messages(rows, delay_ms=delay_ms)[1]]
    return {"results": results, "replies": sum(r["replied"] for r in results)}


def delay_table(loops: dict[int, list[dict]], delays=SWEEP_MS) -> str:
    """How the moment the reply is built changes what goes out (label latency plus the app's pass)."""
    lines = ["| reply built after | replies | wrong price / stock / variant | about an item already off screen | "
             "dropped: item changed |", "|---|---:|---:|---:|---:|"]  # fmt: skip
    for delay in delays:
        res = score(loops, delay)["results"]
        off = sum(r["detail"] == "after_switch" for r in res)
        changed = sum(r["reason"] == "item_changed" for r in res)
        lines.append(f"| {delay} ms | {sum(r['replied'] for r in res)} | {wrong_facts(res).total()} | {off} | "
                     f"{changed} |")  # fmt: skip
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="write every result to this file")
    ap.add_argument("--rescore", action="store_true", help="also score the 31 October paid rescore cases")
    a = ap.parse_args(argv)
    loops = load()
    s = score(loops)
    n = len(s["results"])
    print(f"\n## private replies: {n} comments in {len(loops)} loops, {s['replies']} replies, "
          f"built {DELAY_MS} ms after the comment\n")  # fmt: skip
    print(table(s["results"]))
    print(f"\nwrong price / stock / variant: {dict(wrong_facts(s['results'])) or 0}\n")
    print(delay_table(loops))
    report = {"messages": s}
    if a.rescore:
        r = rescore(loops, runner=run_messages)
        report["rescore"] = r
        print(f"\n## rescore-paid-1031-1615, loop 1, private replies (cards with SKU in that run: "
              f"{', '.join(r['run_cards_with_sku'])})")  # fmt: skip
        for mode in ("a", "b"):
            for group in ("size", "stock"):
                cases = r[mode][group]
                c = Counter(v[1] if v[0] == "correct" else v[0] for v in cases.values())
                print(f"mode {mode} {group}: answered {c['answered']}/{len(cases)}, deferred {c['deferred']}, "
                      f"silent (not known) {c['silent_not_known']}, wrong {c['wrong']}, no answer {c['no_answer']}")  # fmt: skip
            for cid, (outcome, detail, said) in {**r[mode]["size"], **r[mode]["stock"]}.items():
                print(f"  {mode} {cid} {outcome} {detail} | {said}")
    if a.json:
        Path(a.json).write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True, default=str))
    return 1 if wrong_facts(s["results"]).total() else 0


if __name__ == "__main__":
    raise SystemExit(main())
