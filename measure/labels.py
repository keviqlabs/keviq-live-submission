"""python -m measure.labels --session <id> | --events-file FILE [--gold data/comments/labels.jsonl] [--json FILE]

Labels-stream metrics against the hand-labeled comments of data/comments/: micro-F1 over the 7 labels (with
per-label P/R/F1), order-field accuracy, label latency (first and final label, first hide), the scam shield
(hide rate, legit orders hidden) and the cost of `label_comments` model calls. measure.core's C3/C5/labels
are reported on the same gold. A comment with several comment_labeled events counts by its latest one.
"""

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.settings import ROOT
from measure.core import LABELS, c3, c5, labels_f1, latest_labels, pct, when

GOLD = ROOT / "data" / "comments" / "labels.jsonl"
FIELDS = ["sku", "size", "color", "qty", "referenced_card_id", "reference_kind"]
PURPOSE = "label_comments"


def load_gold(path: Path = GOLD) -> dict[str, dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {r["comment_id"]: r for r in rows}


def core_gold(gold: dict[str, dict]) -> dict:
    """The same gold in measure.core's format."""
    out = {}
    for cid, g in gold.items():
        row = {"label": g["label"], "has_phone": g["has_phone"]}
        if g["order"]:
            row["referenced_card_id"] = g["order"]["referenced_card_id"]
        out[cid] = row
    return {"comments": out}


def ratio(a: int, b: int) -> float | None:
    return a / b if b else None


def f1(p: float | None, r: float | None) -> float | None:
    return 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)


def label_f1(events: list[dict], gold: dict[str, dict]) -> dict:
    """Micro-F1 over the 7 labels; an unlabeled comment counts as a miss (recall), not a prediction."""
    labels = latest_labels(events)
    tp, fp, fn = Counter(), Counter(), Counter()
    confusions = Counter()
    for cid, g in gold.items():
        want, got = g["label"], (labels.get(cid) or {}).get("label")
        if got == want:
            tp[want] += 1
            continue
        fn[want] += 1
        if got:
            fp[got] += 1
        confusions[(want, got or "unlabeled")] += 1
    per = {}
    for lab in LABELS:
        p, r = ratio(tp[lab], tp[lab] + fp[lab]), ratio(tp[lab], tp[lab] + fn[lab])
        per[lab] = {"n": tp[lab] + fn[lab], "precision": p, "recall": r, "f1": f1(p, r)}
    p = ratio(sum(tp.values()), sum(tp.values()) + sum(fp.values()))
    r = ratio(sum(tp.values()), len(gold))
    return {
        "n": len(gold),
        "labeled": sum(cid in labels for cid in gold),
        "micro_precision": p,
        "micro_recall": r,
        "micro_f1": f1(p, r),
        "per_label": per,
        "confusions": [{"want": w, "got": g, "n": n} for (w, g), n in confusions.most_common(10)],
    }


def draft_fields(draft: dict | None) -> dict:
    if not draft:
        return dict.fromkeys(FIELDS)
    variant = draft.get("variant") or {}
    return {
        "sku": draft["sku"],
        "size": variant.get("size"),
        "color": variant.get("color"),
        "qty": draft["qty"],
        "referenced_card_id": draft["referenced_card_id"],
        "reference_kind": draft["reference_kind"],
    }


def order_fields(events: list[dict], gold: dict[str, dict]) -> dict:
    """Per-field accuracy of order_draft over the gold orders (a missing draft gets every field wrong)."""
    labels = latest_labels(events)
    right, exact, wrong = Counter(), 0, []
    orders = {cid: g["order"] for cid, g in gold.items() if g["label"] == "order"}
    by_kind = defaultdict(lambda: [0, 0])
    for cid, want in orders.items():
        got = draft_fields((labels.get(cid) or {}).get("order_draft"))
        bad = [f for f in FIELDS if got[f] != want[f]]
        right.update(f for f in FIELDS if f not in bad)
        exact += not bad
        by_kind[want["reference_kind"]][0] += not bad
        by_kind[want["reference_kind"]][1] += 1
        if bad:
            wrong.append({"comment_id": cid, **{f: {"want": want[f], "got": got[f]} for f in bad}})
    n = len(orders)
    return {
        "n": n,
        "fields": {f: ratio(right[f], n) for f in FIELDS},
        "all_exact": ratio(exact, n),
        "by_reference_kind": {k: {"exact": a, "n": b} for k, (a, b) in sorted(by_kind.items())},
        "wrong": wrong[:15],
    }


def stats(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50": pct(values, 50),
        "p95": pct(values, 95),
        "max": max(values, default=None),
    }


def latency(events: list[dict]) -> dict:
    """Arrival of the comment -> arrival of its first label, its final label and its first hide (ms)."""
    comments = {e["comment_id"]: e for e in events if e["type"] == "comment"}
    first, final, hide = {}, {}, {}
    infer, model = [], []
    for e in events:
        if e["type"] != "comment_labeled" or e["comment_id"] not in comments:
            continue
        cid = e["comment_id"]
        first.setdefault(cid, e)
        final[cid] = e
        if e["action"] == "hide":
            hide.setdefault(cid, e)
        t = e.get("timing") or {}
        if "infer_start_ms" in t:
            infer.append(t["infer_end_ms"] - t["infer_start_ms"])
    for e in events:
        if e["type"] == "model_call" and e["purpose"] == PURPOSE:
            model.append(e["latency_ms"])

    def ms(d):
        return [when(lab) - when(comments[cid]) for cid, lab in d.items()]

    return {
        "first_label_ms": stats(ms(first)),
        "final_label_ms": stats(ms(final)),
        "first_hide_ms": stats(ms(hide)),
        "batch_infer_ms": stats(infer),
        "model_call_ms": stats(model),
    }


def shield(events: list[dict], gold: dict[str, dict]) -> dict:
    """Scams with a phone number or a link must be hidden; other scams hidden or warned; buyers never warned."""
    labels = latest_labels(events)
    hidden = {e["comment_id"] for e in events if e["type"] == "comment_labeled" and e["action"] == "hide"}

    def action(cid):
        return (labels.get(cid) or {}).get("action")

    scams = [cid for cid, g in gold.items() if g["label"] == "scam"]
    contact = [cid for cid in scams if gold[cid]["has_phone"] or gold[cid]["has_link"]]
    phones = [cid for cid, g in gold.items() if g["has_phone"]]
    orders = [cid for cid, g in gold.items() if g["label"] == "order"]
    return {
        "scam_n": len(scams),
        "scam_with_contact_n": len(contact),
        "scam_contact_hide_rate": ratio(sum(action(c) == "hide" for c in contact), len(contact)),
        "scam_hide_or_warn_rate": ratio(sum(action(c) in ("hide", "warn") for c in scams), len(scams)),
        "phone_n": len(phones),
        "phone_hidden_rate": ratio(sum(c in hidden for c in phones), len(phones)),
        "orders_hidden": sorted(c for c in orders if c in hidden),
        "orders_warned": sorted(c for c in orders if action(c) == "warn"),
        "scams_missed": sorted(c for c in scams if action(c) not in ("hide", "warn")),
    }


def model_cost(events: list[dict]) -> dict:
    calls = [e for e in events if e["type"] == "model_call" and e["purpose"] == PURPOSE]
    return {
        "calls": len(calls),
        "errors": sum(not e["ok"] for e in calls),
        "models": sorted({e["model"] for e in calls}),
        "tokens_in": sum(e["tokens_in"] for e in calls),
        "tokens_out": sum(e["tokens_out"] for e in calls),
        "cost_usd": round(sum(e["cost_usd"] for e in calls), 6),
    }


def report(events: list[dict], gold: dict[str, dict]) -> dict:
    seen = {e["comment_id"] for e in events if e["type"] == "comment"}
    gold = {cid: g for cid, g in gold.items() if cid in seen}
    cg = core_gold(gold)
    return {
        "comments": len(gold),
        "labels": label_f1(events, gold),
        "order_fields": order_fields(events, gold),
        "latency": latency(events),
        "shield": shield(events, gold),
        "cost": model_cost(events),
        "core": {"c3": c3(events, cg), "c5": c5(events, cg), "labels": labels_f1(events, cg)},
    }


def fmt(v, digits=3):
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}" if isinstance(v, float) else str(v)


def table(r: dict) -> str:
    lab, of, lat, sh, co = r["labels"], r["order_fields"], r["latency"], r["shield"], r["cost"]
    lines = [
        f"comments with gold: {r['comments']}",
        f"labels micro-F1: {fmt(lab['micro_f1'])} on n={lab['n']} ({lab['labeled']} labeled;"
        f" P={fmt(lab['micro_precision'])} R={fmt(lab['micro_recall'])}; target >= 0.85)",
    ]
    for name, v in lab["per_label"].items():
        lines.append(
            f"  {name:<15} n={v['n']:<3} P={fmt(v['precision'])} R={fmt(v['recall'])} F1={fmt(v['f1'])}"
        )
    if lab["confusions"]:
        lines.append(
            "  top confusions: "
            + ", ".join(f"{c['want']}->{c['got']} x{c['n']}" for c in lab["confusions"][:5])
        )
    lines.append(f"order fields on n={of['n']} gold orders: all exact {fmt(of['all_exact'])}")
    lines.append("  " + "  ".join(f"{f}={fmt(v)}" for f, v in of["fields"].items()))
    lines.append(
        "  exact by reference_kind: "
        + ", ".join(f"{k} {v['exact']}/{v['n']}" for k, v in of["by_reference_kind"].items())
    )
    lines.append("latency ms (p50 / p95 / max, n), comment arrival -> label arrival:")
    for name, v in lat.items():
        lines.append(f"  {name:<15} {fmt(v['p50'])} / {fmt(v['p95'])} / {fmt(v['max'])}  n={v['n']}")
    lines.append(
        f"shield: scams with contact hidden {fmt(sh['scam_contact_hide_rate'])} on n={sh['scam_with_contact_n']};"
        f" scams hidden or warned {fmt(sh['scam_hide_or_warn_rate'])} on n={sh['scam_n']};"
        f" phone comments hidden {fmt(sh['phone_hidden_rate'])} on n={sh['phone_n']}"
    )
    lines.append(
        f"  orders hidden (own phone number): {sh['orders_hidden']}  orders warned: {sh['orders_warned']}"
        f"  scams missed: {sh['scams_missed']}"
    )
    lines.append(
        f"cost {PURPOSE}: {co['calls']} calls, {co['errors']} errors, {co['tokens_in']} in /"
        f" {co['tokens_out']} out tokens, ${co['cost_usd']:.4f} {co['models']}"
    )
    c3_, c5_ = r["core"]["c3"], r["core"]["c5"]
    lines.append(
        f"core: C3 {fmt(c3_['accuracy'])} on n={c3_['n']}; C5 order recall {fmt(c5_['order_recall'])} on"
        f" n={c5_['order_n']}; phone visible max {fmt(c5_['phone_visible_max_s'], 1)} s on n={c5_['phone_n']}"
        f" (measured at the latest label)"
    )
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(prog="measure.labels")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--session")
    src.add_argument("--events-file")
    p.add_argument("--gold", default=str(GOLD))
    p.add_argument("--json", help="also write the report as JSON here")
    args = p.parse_args(argv)
    if args.events_file:
        events = [json.loads(x) for x in Path(args.events_file).read_text().splitlines() if x.strip()]
    else:
        from measure.__main__ import from_store

        events = asyncio.run(from_store(args.session))
    r = report(events, load_gold(Path(args.gold)))
    print(table(r))
    if args.json:
        Path(args.json).write_text(json.dumps(r, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
