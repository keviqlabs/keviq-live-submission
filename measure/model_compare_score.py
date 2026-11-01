"""Model comparison for product identity: frame selection, what each schema's output picks, and the scores.

Pure functions only (no network, no files beyond what the caller passes). The runner is measure/model_compare.py.

Behaviour, not self-reported confidence: every read ends in one of
  pick (an item code), abstain (the model said none / unsure / unclear), cannot_name (the schema's answer maps to
  no single item: `collision` when several items share the category and colour, `no_match` when none has them),
  error (no valid output).
The production gate (confidence < MIN_CONFIDENCE turns a pick into an abstain) is a second, separate view.
"""

import math
from collections import Counter, defaultdict

from measure.core import pct

MIN_CONFIDENCE = 0.5  # worker/vision/spark.py: a read under it is dropped
SKUS = [f"SP{i:02d}" for i in range(1, 11)]
STRATA = ("single_unique_colour", "same_colour_fashion", "same_colour_other", "boundary", "no_product")
CLEAR = STRATA[:3]


def colour_groups(catalog: list[dict]) -> dict[str, str]:
    """sku -> stratum for a clear frame: its colour is unique in the catalog, or shared with an item of another
    kind (đen/trắng across shoes and shirts), or shared within the items production can only call `other`."""
    colours = Counter(colour_of(i) for i in catalog)
    out = {}
    for item in catalog:
        if colours[colour_of(item)] == 1:
            out[item["sku"]] = "single_unique_colour"
        elif (item.get("categories") or [item["category"]]) == ["other"]:
            out[item["sku"]] = "same_colour_other"
        else:
            out[item["sku"]] = "same_colour_fashion"
    return out


def colour_of(item: dict) -> str:
    colours = {v["color"] for v in item["variants"]}
    assert len(colours) == 1, item["sku"]
    return colours.pop()


def spread(values: list, k: int) -> list:
    """k values evenly spaced over a sorted list (the middle of k equal parts); all of them when k >= len."""
    n = len(values)
    if k >= n:
        return list(values)
    return [values[math.floor((i + 0.5) * n / k)] for i in range(k)]


def allowed_at(t: int, segments: list[dict]) -> list[str]:
    """Items a boundary second may show: both sides of every product change whose band holds t."""
    out = set()
    for prev, seg in zip(segments, segments[1:], strict=False):
        a, b = seg["change_band_s"]
        if a <= t <= b:
            out |= {prev["product"], seg["product"]}
    return sorted(x for x in out if x)


def select(
    labels: dict,
    catalog: list[dict],
    comments: list[dict],
    per_sku: int = 4,
    no_product: int = 6,
    last_frame: int = 349,
) -> list[dict]:
    """The frozen frame list. Clear frames: `per_sku` non-boundary seconds per item, evenly spaced. Boundary: the
    second nearest each product change's cue. No product: `no_product` non-boundary seconds with no item. Order
    comments: the second each one's card would come from (the comment's second for the current card, the last
    clear second of the gold item before it for the previous card), tagged with the comment id."""
    seconds = {s["t"]: s for s in labels["seconds"] if s["t"] <= last_frame}
    group = colour_groups(catalog)
    picked: dict[int, dict] = {}

    def add(t: int, why: str, comment: str | None = None) -> None:
        s = seconds[t]
        if s["boundary"]:
            stratum = "boundary"
        elif s["product"] is None:
            stratum = "no_product"
        else:
            stratum = group[s["product"]]
        row = picked.setdefault(
            t,
            {
                "t": t,
                "frame": f"f_{t:05d}.jpg",
                "gold": s["product"],
                "kind": s["kind"],
                "color": s["color"],
                "stratum": stratum,
                "allowed": allowed_at(t, labels["segments"]) if s["boundary"] else [],
                "why": [],
                "comments": [],
            },
        )
        if why not in row["why"]:
            row["why"].append(why)
        if comment:
            row["comments"].append(comment)

    for sku in SKUS:
        clear = sorted(t for t, s in seconds.items() if s["product"] == sku and not s["boundary"])
        for t in spread(clear, per_sku):
            add(t, "clear")
    for seg in labels["segments"][1:]:
        t = math.floor(seg["cue_s"] + 0.5)
        if t in seconds and seconds[t]["boundary"]:
            add(t, "boundary")
    empty = sorted(t for t, s in seconds.items() if s["product"] is None and not s["boundary"])
    for t in spread(empty, no_product):
        add(t, "no_product")
    for c in comments:
        sec = c["ts_ms"] // 1000
        gold = c["order"]["product"]
        if c["order"]["reference_kind"] == "previous_card":
            sec = max(t for t, s in seconds.items() if t < sec and s["product"] == gold and not s["boundary"])
        add(sec, "order_comment", c["comment_id"])
    return run_order(sorted(picked.values(), key=lambda r: r["t"]))


def run_order(rows: list[dict]) -> list[dict]:
    """Interleave the groups (each clear item, boundary, no product) so any prefix of the list is spread over
    them: a run stopped by the cap still compares the arms on a balanced set."""
    groups = defaultdict(list)
    for r in rows:
        groups[r["gold"] if r["stratum"] in CLEAR else r["stratum"]].append(r)
    keyed = []
    for name, members in groups.items():
        for i, r in enumerate(members):
            keyed.append(((i + 0.5) / len(members), name, r["t"], r))
    out = []
    for n, (_, _, _, r) in enumerate(sorted(keyed, key=lambda x: x[:3])):
        out.append(r | {"order": n})
    return out


# --- what an output picks ---------------------------------------------------------------------------------


def match(catalog: list[dict], category: str, color: str | None) -> tuple[str | None, str]:
    """worker/brain/card.py `match` on this catalog: items of the category (an item with `categories` is of each),
    then of the colour when one is given (exact string). (sku, "pick") when one item is left, else
    (None, "collision" | "no_match")."""
    same = [i for i in catalog if category in (i.get("categories") or [i["category"]])]
    if color:
        same = [i for i in same if any(v["color"] == color for v in i["variants"])]
    if len(same) == 1:
        return same[0]["sku"], "pick"
    return None, ("collision" if same else "no_match")


def match_kind(catalog: list[dict], kind: str, color: str | None) -> tuple[str | None, str]:
    same = [i for i in catalog if i["kind"] == kind and (not color or colour_of(i) == color)]
    if len(same) == 1:
        return same[0]["sku"], "pick"
    return None, ("collision" if same else "no_match")


def norm(color) -> str | None:
    """worker/vision/spark.py: colour stripped and lower-cased, empty -> None."""
    return color.strip().lower() if isinstance(color, str) and color.strip() else None


def decide(schema: str, data: dict | None, catalog: list[dict]) -> dict:
    """{"sku", "outcome": pick|abstain|cannot_name|error, "why", "confidence"} for one output, before the gate.

    A (production schema): no product or category none -> abstain; else the catalog match of category + colour.
    B0 (kind + colour, no item list): state other than one_product, or kind none / khác -> abstain; else the one
    item of that kind and colour. B (with the item list): the `sku` field; unsure / none -> abstain."""
    if data is None:
        return {"sku": None, "outcome": "error", "why": "error", "confidence": None}
    conf = data.get("confidence")
    if schema == "A":
        if not data["has_product"] or data["category"] == "none":
            return {"sku": None, "outcome": "abstain", "why": "none", "confidence": conf}
        sku, why = match(catalog, data["category"], norm(data["color"]))
    elif schema == "B0":
        if data["state"] != "one_product":
            return {"sku": None, "outcome": "abstain", "why": data["state"], "confidence": conf}
        if data["kind"] in ("none", "khác"):
            return {"sku": None, "outcome": "abstain", "why": data["kind"], "confidence": conf}
        sku, why = match_kind(catalog, data["kind"], norm(data["color"]))
    elif schema == "B":
        if data["sku"] not in SKUS:
            return {"sku": None, "outcome": "abstain", "why": data["sku"], "confidence": conf}
        sku, why = data["sku"], "pick"
    else:
        raise ValueError(schema)
    return {"sku": sku, "outcome": "pick" if sku else "cannot_name", "why": why, "confidence": conf}


def gated(d: dict) -> dict:
    """Production's gate on top: a pick with confidence under MIN_CONFIDENCE is dropped (an abstain)."""
    if d["outcome"] == "pick" and (d["confidence"] is None or d["confidence"] < MIN_CONFIDENCE):
        return d | {"sku": None, "outcome": "abstain", "why": "low_confidence"}
    return d


def verdict(frame: dict, d: dict) -> str:
    """correct | wrong | abstain | cannot_name | error. Boundary: a pick of either side of the change is correct.
    No product: any pick is wrong."""
    if d["outcome"] != "pick":
        return d["outcome"]
    if frame["stratum"] == "boundary":
        return "correct" if d["sku"] in frame["allowed"] else "wrong"
    return "correct" if d["sku"] == frame["gold"] else "wrong"


def attrs_right(schema: str, data: dict | None, frame: dict, category_map: dict) -> bool | None:
    """Did the output name the right kind and colour (whatever item it maps to)? None off clear frames."""
    if frame["stratum"] not in CLEAR:
        return None
    if data is None:
        return False
    if schema == "A":
        return data["category"] in category_map[frame["kind"]] and norm(data["color"]) == frame["color"]
    return data["kind"] == frame["kind"] and norm(data["color"]) == frame["color"]


# --- scores -----------------------------------------------------------------------------------------------

VERDICTS = ("correct", "wrong", "abstain", "cannot_name", "error")


def counts(rows: list[dict], key: str) -> dict:
    c = Counter(r[key] for r in rows)
    return {v: c.get(v, 0) for v in VERDICTS} | {"n": len(rows)}


def arm_score(rows: list[dict], frames: int = 350) -> dict:
    """One config x schema: behaviour by stratum (headline), the gated view, attributes, latency and cost."""
    by = defaultdict(list)
    for r in rows:
        by[r["stratum"]].append(r)
    clear = [r for r in rows if r["stratum"] in CLEAR]
    hard = [r for r in rows if r["stratum"] in ("boundary", "no_product")]
    ok = [r for r in rows if r["ok"]]
    lat = [r["latency_ms"] for r in ok]
    usd = sum(r["cost_usd"] for r in rows)
    picks = [r for r in rows if r["verdict"] in ("correct", "wrong")]

    def no_pick(rs):
        return sum(r["verdict"] in ("abstain", "cannot_name", "error") for r in rs)

    return {
        "n": len(rows),
        "by_stratum": {s: counts(by[s], "verdict") for s in STRATA if by[s]},
        "clear": counts(clear, "verdict"),
        "all": counts(rows, "verdict"),
        "gated": {
            "by_stratum": {s: counts(by[s], "verdict_gated") for s in STRATA if by[s]},
            "clear": counts(clear, "verdict_gated"),
            "all": counts(rows, "verdict_gated"),
        },
        "exact_clear": ratio(sum(r["verdict"] == "correct" for r in clear), len(clear)),
        "wrong_rate_all": ratio(sum(r["verdict"] == "wrong" for r in rows), len(rows)),
        "pick_precision": ratio(sum(r["verdict"] == "correct" for r in picks), len(picks)),
        "no_pick_rate_clear": ratio(no_pick(clear), len(clear)),
        "no_pick_rate_hard": ratio(no_pick(hard), len(hard)),
        "attrs_right_clear": ratio(sum(bool(r["attrs_right"]) for r in clear), len(clear)),
        "per_sku_clear": {
            sku: f"{sum(r['verdict'] == 'correct' for r in clear if r['gold'] == sku)}/"
            f"{sum(r['gold'] == sku for r in clear)}"
            for sku in SKUS
            if any(r["gold"] == sku for r in clear)
        },
        "why_no_pick": dict(Counter(r["why"] for r in rows if r["verdict"] in ("abstain", "cannot_name"))),
        "latency_ms": {
            "n": len(lat),
            "p50": pct(lat, 50),
            "p95": pct(lat, 95),
            "max": max(lat, default=None),
        },
        "usd": {
            "total": round(usd, 8),
            "per_call": round(usd / len(rows), 8) if rows else None,
            f"per_{frames}_frames": round(usd / len(rows) * frames, 6) if rows else None,
        },
        "tokens": {
            "in_mean": round(sum(r["tokens_in"] for r in ok) / len(ok), 1) if ok else None,
            "out_mean": round(sum(r["tokens_out"] for r in ok) / len(ok), 1) if ok else None,
            "out_max": max((r["tokens_out"] for r in ok), default=None),
        },
    }


def ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def score(rows: list[dict], frames: list[dict], catalog: list[dict], category_map: dict) -> dict:
    """rows: the run's per-call rows (t, config, schema, ok, data, cost_usd, latency_ms, tokens). Adds the
    decision and verdicts to each row. Returns {"arms": {config/schema: score}, "paired": {schema: ...},
    "best": ..., "rows": [...]}. `paired` scores, per schema, only the frames every config read under that
    schema, so an arm stopped early by the cap is compared on the same frames."""
    by_t = {f["t"]: f for f in frames}
    scored = []
    for r in rows:
        f = by_t[r["t"]]
        data = r["data"] if r["ok"] else None
        d = decide(r["schema"], data, catalog)
        scored.append(
            r
            | {
                "stratum": f["stratum"],
                "gold": f["gold"],
                "allowed": f["allowed"],
                "sku": d["sku"],
                "why": d["why"],
                "verdict": verdict(f, d),
                "verdict_gated": verdict(f, gated(d)),
                "attrs_right": attrs_right(r["schema"], data, f, category_map),
            }
        )
    arms = defaultdict(list)
    for r in scored:
        arms[arm_of(r)].append(r)
    out = {"arms": {k: arm_score(v) for k, v in sorted(arms.items())}, "paired": {}}
    for schema in sorted({r["schema"] for r in scored}):
        names = sorted(k for k in arms if k.endswith("/" + schema))
        common = set.intersection(*({r["t"] for r in arms[k]} for k in names))
        out["paired"][schema] = {
            "frames": len(common),
            "arms": {k: arm_score([r for r in arms[k] if r["t"] in common]) for k in names},
        }
    out["best"] = best_arm(out["arms"], len(frames))
    out["rows"] = scored
    return out


def arm_of(r: dict) -> str:
    return f"{r['config']}/{r['schema']}"


BEST_RULE = (
    "Among arms that read every manifest frame with at most 5% error rows and abstain or cannot name on at most "
    "half of the clear frames: the fewest wrong picks over all frames (ungated); ties -> more correct clear "
    "frames; then the lower USD."
)


def best_arm(arms: dict, frames: int) -> str | None:
    ok = [
        (k, a)
        for k, a in arms.items()
        if a["n"] == frames
        and a["all"]["error"] <= 0.05 * frames
        and a["no_pick_rate_clear"] is not None
        and a["no_pick_rate_clear"] <= 0.5
    ]
    if not ok:
        return None
    return min(ok, key=lambda x: (x[1]["all"]["wrong"], -x[1]["clear"]["correct"], x[1]["usd"]["total"]))[0]


def blockers(comment: dict, catalog: list[dict], price_changes: list[dict]) -> list[str]:
    """What still stands between this order comment and an order once its item is known, from the comment and
    the catalog alone (no model call): reference, size / variant, stock, a spoken price change."""
    o = comment["order"]
    item = next(i for i in catalog if i["sku"] == o["product"])
    sizes = [v["size"] for v in item["variants"] if v.get("size")]
    out = []
    if o["reference_kind"] == "previous_card":
        out.append("reference: names an earlier item ('vừa nãy'), not the one on screen")
    if sizes and not o.get("size"):
        out.append(f"size: none given, item has {sizes}; ask the customer")
    elif sizes and o["size"] not in sizes:
        out.append(f"size: {o['size']} is not a variant of {item['sku']} ({sizes})")
    if not sizes or o.get("size") in sizes:  # stock is per variant: checked once the variant is known
        stock = sum(v["stock"] for v in item["variants"] if not sizes or v["size"] == o["size"])
        if o.get("qty") and o["qty"] > stock:
            out.append(f"stock: qty {o['qty']} > stock {stock}; seller decides")
    for pc in price_changes:
        if pc["sku"] == item["sku"] and pc["from_s"] * 1000 <= comment["ts_ms"]:
            out.append(
                f"price: spoken {pc['new_price']} vs catalog {item['price']} from {pc['from_s']} s; "
                "the change needs seller approval"
            )
    return out


def comment_links(
    scored: list[dict], arm: str, frames: list[dict], comments: list[dict], catalog, price_changes
) -> list[dict]:
    """For each order comment left without a card: the arm's read of the frame its card would come from, the
    arm's reads of the gold item's clear manifest frames, and what still blocks the order."""
    rows = [r for r in scored if arm_of(r) == arm]
    by_t = {r["t"]: r for r in rows}
    out = []
    for c in comments:
        gold = c["order"]["product"]
        frame = next(f for f in frames if c["comment_id"] in f["comments"])
        r = by_t.get(frame["t"])
        item = [x for x in rows if x["gold"] == gold and x["stratum"] in CLEAR]
        out.append(
            {
                "comment_id": c["comment_id"],
                "gold": gold,
                "reference_kind": c["order"]["reference_kind"],
                "frame_t": frame["t"],
                "read": None if r is None else {"sku": r["sku"], "verdict": r["verdict"], "why": r["why"]},
                "valid_sku_card": r is not None and r["verdict"] == "correct",
                "item_frames_correct": f"{sum(x['verdict'] == 'correct' for x in item)}/{len(item)}",
                "still_blocks": blockers(c, catalog, price_changes),
            }
        )
    return out
