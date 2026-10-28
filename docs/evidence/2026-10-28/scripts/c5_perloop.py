"""Per-loop view of one scored C5 run file: ids and numbers only, no text, no media.

Splits the scorer's own rows by loop (comment c5-K-NNN, order o-c5-K-NNN, intervention/proposal `loop`).
No rescoring: every number is a count over rows the scorer already wrote.

  python3 c5_perloop.py RUN.json > RUN-perloop.json
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

d = json.loads(Path(sys.argv[1]).read_text())
sc = d["score"]
sku = sc["card_sku"]


def loop_of(cid):
    m = re.match(r"(?:o-)?c5-(\d+)-", cid or "")
    return int(m.group(1)) if m else None


by = defaultdict(lambda: defaultdict(list))
for r in sc["order_ids"]["rows"]:
    by[loop_of(r["comment_id"])]["ids"].append(r)
for r in sc["interventions"]["rows"]:
    by[r["loop"]]["iv"].append(r)
for r in sc["proposals"].get("rows", []):
    by[r["loop"]]["pr"].append(r)
for r in sc["receipts"]["rows"]:
    by[loop_of(r["order_id"])]["rc"].append(r)

ids_by_comment = {r["comment_id"]: r for r in sc["order_ids"]["rows"]}
out = []
for k in sorted(x for x in by if x is not None):
    g = by[k]
    rows = g["ids"]
    gold = [r for r in rows if r["in_n"]]
    unres = [r["comment_id"] for r in rows if r["gold_kind"] == "unresolved"]
    kinds = defaultdict(lambda: [0, 0])
    for r in gold:
        kinds[r["gold_kind"]][0] += 1
        kinds[r["gold_kind"]][1] += r["right"]
    rc = []
    for r in g["rc"]:
        src = r.get("image_source") or {}
        row = ids_by_comment.get(r["order_id"][2:]) or {}
        card = src.get("card_id")
        item = sku.get(card)
        rc.append({"order_id": r["order_id"], "card_id": card, "card_version": src.get("card_version"),
                   "card_item": item, "gold_item": row.get("gold_item"),
                   "right_item": item is not None and item == row.get("gold_item"),
                   "draft_card_same": row.get("card") == card, "version_ok": r["version_ok"],
                   "image": r["image"], "image_path": src.get("image_path"),
                   "image_sha256": src.get("image_sha256"), "missing": src.get("missing")})  # fmt: skip
    pr = g["pr"]
    out.append({
        "loop": k,
        "order_comments": len(rows),
        "with_gold_item": len(gold),
        "unresolved": unres,
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "references": {"right": sum(r["right"] for r in gold), "n": len(gold),
                       "by_kind": {kk: {"n": v[0], "right": v[1]} for kk, v in sorted(kinds.items())},
                       "why_wrong": dict(Counter(r["why"] for r in gold if not r["right"]))},
        "interventions": {"n": len(g["iv"]), "by_cause": dict(Counter(
            f"{r['kind']}:{'+'.join(r['cause']) if isinstance(r['cause'], list) else r['cause']}" for r in g["iv"]))},
        "proposals": {"n": len(pr), "right_card": sum(bool(r["right_card"]) for r in pr),
                      "right_price": sum(bool(r["right_price"]) for r in pr),
                      "both": sum(bool(r["right_card"] and r["right_price"]) for r in pr),
                      "stale_on_arrival": [r["event_id"] for r in pr if r["stale"]],
                      "outcomes": dict(Counter(r["outcome"] for r in pr))},
        "receipts": {"n": len(rc), "version_ok": sum(r["version_ok"] for r in rc),
                     "right_item": sum(r["right_item"] for r in rc), "with_image": sum(bool(r["image"]) for r in rc),
                     "rows": rc},
    })  # fmt: skip

tot = {"order_comments": sum(x["order_comments"] for x in out), "with_gold_item": sum(x["with_gold_item"] for x in out),
       "unresolved": sum(len(x["unresolved"]) for x in out),
       "references_right": sum(x["references"]["right"] for x in out),
       "interventions": sum(x["interventions"]["n"] for x in out),
       "stale_on_arrival": sum(len(x["proposals"]["stale_on_arrival"]) for x in out),
       "receipts": sum(x["receipts"]["n"] for x in out),
       "receipts_right_item": sum(x["receipts"]["right_item"] for x in out),
       "receipts_with_image": sum(x["receipts"]["with_image"] for x in out),
       "scorer": {"order_ids_n": sc["order_ids"]["n"], "order_ids_in_n": sc["order_ids"]["in_n"],
                  "references": {k: sc["references"][k] for k in ("n", "right", "accuracy", "gate")},
                  "interventions_total": sc["interventions"]["total"],
                  "proposals_stale": sc["proposals"]["stale"]["n"],
                  "receipts": {k: v for k, v in sc["receipts"].items() if k != "rows"}}}  # fmt: skip
print(json.dumps({"run": d["run_file"]["run"], "loops": out, "total": tot}, indent=1, ensure_ascii=False))
