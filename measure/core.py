"""Pure measurement functions over a list of events (one session). Inputs are contract events; a replay file
may also carry `_replay_at_ms`, and events read back from the store carry `ingested_ms`."""

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

VN = timezone(timedelta(hours=7))
LABELS = ["order", "price_question", "stock_question", "complaint", "spam", "scam", "other"]
CYCLE_MS = 5000
NEEDS_CONSEQUENCE = {"order", "price_question", "stock_question", "complaint"}


def when(e: dict) -> int:
    """Arrival time: ingest time when known, else replay arrival, else source time."""
    return e.get("ingested_ms") or e.get("_replay_at_ms") or e["ts_ms"]


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))]


def cost(events: list[dict]) -> list[dict]:
    rows = defaultdict(lambda: {"calls": 0, "errors": 0, "cost_usd": 0.0})
    for e in events:
        if e["type"] == "model_call":
            day = datetime.fromtimestamp(e["ts_ms"] / 1000, VN).date().isoformat()
            r = rows[(day, e["model"])]
            r["calls"] += 1
            r["errors"] += 0 if e["ok"] else 1
            r["cost_usd"] += e["cost_usd"]
    return [
        {"day": d, "model": m, **r, "cost_usd": round(r["cost_usd"], 6)} for (d, m), r in sorted(rows.items())
    ]


def latency(events: list[dict]) -> dict:
    """C8: p50/p95 per stage (receive, wait, infer, display) per emitted event type, from `timing`."""
    by_id = {e["event_id"]: e for e in events}
    stages = defaultdict(lambda: defaultdict(list))
    for e in events:
        t = e.get("timing")
        if not t or "received_ms" not in t:
            continue
        trig = by_id.get((e.get("caused_by") or [None])[0])
        s = stages[e["type"]]
        if trig:
            s["receive"].append(t["received_ms"] - when(trig))
        if "infer_start_ms" in t:
            s["wait"].append(t["infer_start_ms"] - t["received_ms"])
            s["infer"].append(t["infer_end_ms"] - t["infer_start_ms"])
        else:
            s["wait"].append(t["emitted_ms"] - t["received_ms"])
        if e.get("ingested_ms"):
            s["display"].append(e["ingested_ms"] - t["emitted_ms"])
    return {
        etype: {name: {"n": len(v), "p50": pct(v, 50), "p95": pct(v, 95)} for name, v in s.items()}
        for etype, s in stages.items()
    }


def latest_labels(events: list[dict]) -> dict[str, dict]:
    return {e["comment_id"]: e for e in events if e["type"] == "comment_labeled"}


def c1(events: list[dict], cycle_ms: int = CYCLE_MS) -> dict:
    """Every product_seen and every actionable comment_labeled has a consequence within one cycle.

    Consequence = a later event that lists the trigger in caused_by. A comment_labeled whose action is
    hide/warn is its own consequence; label `other` with action none needs none (docs/DECISIONS.md)."""
    consequences = defaultdict(list)
    for e in events:
        for c in e.get("caused_by") or []:
            consequences[c].append(e)
    missing, n = [], 0
    for e in events:
        if e["type"] == "product_seen":
            pass
        elif e["type"] == "comment_labeled":
            if e["action"] in ("hide", "warn") or e["label"] not in NEEDS_CONSEQUENCE:
                continue
        else:
            continue
        n += 1
        if not any(0 <= when(c) - when(e) <= cycle_ms for c in consequences[e["event_id"]]):
            missing.append(e["event_id"])
    return {"n": n, "covered": n - len(missing), "missing": missing, "cycle_ms": cycle_ms}


def c3(events: list[dict], gold: dict) -> dict:
    labels = latest_labels(events)
    rows = [
        (cid, g["referenced_card_id"]) for cid, g in gold["comments"].items() if "referenced_card_id" in g
    ]
    wrong = []
    for cid, want in rows:
        draft = (labels.get(cid) or {}).get("order_draft") or {}
        if draft.get("referenced_card_id") != want:
            wrong.append({"comment_id": cid, "want": want, "got": draft.get("referenced_card_id")})
    n = len(rows)
    return {
        "n": n,
        "accuracy": (n - len(wrong)) / n if n else None,
        "target": 0.9,
        "target_n": 50,
        "wrong": wrong,
    }


def labels_f1(events: list[dict], gold: dict) -> dict:
    labels = latest_labels(events)
    gold_c = gold["comments"]
    predicted = [cid for cid in gold_c if cid in labels]
    correct = sum(labels[cid]["label"] == gold_c[cid]["label"] for cid in predicted)
    precision = correct / len(predicted) if predicted else None
    recall = correct / len(gold_c) if gold_c else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
    return {"n": len(gold_c), "labeled": len(predicted), "micro_f1": f1}


def c5(events: list[dict], gold: dict) -> dict:
    labels = latest_labels(events)
    comments = {e["comment_id"]: e for e in events if e["type"] == "comment"}
    gold_orders = [cid for cid, g in gold["comments"].items() if g["label"] == "order"]
    hit = sum((labels.get(cid) or {}).get("label") == "order" for cid in gold_orders)
    span_ms = (max(when(e) for e in events) - min(when(e) for e in events)) if events else 0
    cards = {(e["card_id"], e["version"]): e for e in events if e["type"] == "card"}
    # a seller version counts when it changes the price: worker and retire versions copy edited_by at the same price
    interventions = 0
    for e in events:
        if e["type"] == "card" and e["provenance"]["edited_by"] == "seller":
            before = cards.get((e["card_id"], e["version"] - 1))
            interventions += before is None or before.get("price") != e.get("price")
        elif e["type"] == "order":
            interventions += e["actor"] == "seller"
    hidden_at = {}  # a phone stops being visible at its first hide (shield or model), not at the latest label
    for e in events:
        if e["type"] == "comment_labeled" and e["action"] == "hide":
            hidden_at[e["comment_id"]] = min(when(e), hidden_at.get(e["comment_id"], when(e)))
    phone_s = []
    for cid, g in gold["comments"].items():
        if g.get("has_phone") and cid in comments:
            phone_s.append((hidden_at[cid] - when(comments[cid])) / 1000 if cid in hidden_at else math.inf)
    receipts = [e for e in events if e["type"] == "order" and e["state"] == "CONFIRMED"]
    good = 0
    for o in receipts:
        r, line = o.get("receipt") or {}, o["lines"][0]
        card = cards.get((r.get("card_id", line["card_id"]), r.get("card_version")))
        good += bool(
            r.get("image_url")
            and card
            and r["price"] == card["price"]
            and r["image_url"] == card["image_url"]
        )
    hours = span_ms / 3_600_000
    return {
        "order_recall": hit / len(gold_orders) if gold_orders else None,
        "order_n": len(gold_orders),
        "interventions": interventions,
        "interventions_per_hour": interventions / hours if hours else None,
        "session_minutes": round(span_ms / 60000, 2),
        "phone_visible_max_s": max(phone_s) if phone_s else None,
        "phone_n": len(phone_s),
        "receipts": len(receipts),
        "receipts_ok": good,
    }


def report(events: list[dict], gold: dict | None) -> dict:
    fixtures = sum(e["event_id"].split(".")[-1].startswith("fx-") for e in events)
    out = {
        "events": len(events),
        "fixtures": fixtures,
        "cost": cost(events),
        "c8_latency": latency(events),
        "c1": c1(events),
    }
    if gold:
        out |= {"c3": c3(events, gold), "c5": c5(events, gold), "labels": labels_f1(events, gold)}
    return out
