"""C5 scoring: one run of test1 x5 (measure/c5.py) -> the numbers of the C5 report. Pure: events in, dict out.

Inputs: the session's events in store order; the run (`loops`: k, t0_ms, end_ms, the `price wait:` log lines of
the loop and the `Session.waits` snapshot taken at its end, before the restart; the shop and customer logs); the
C5 streams and messenger rows; the gold (c5_shop.Gold) and the C5 catalog. Nothing here returns comment text,
an utterance or a phone number: rows are named by comment id, msg id and event id only.
"""

import base64
import hashlib
import re
from collections import Counter, defaultdict
from statistics import median

from measure import core
from measure.c5_shop import Gold, Loops, cause
from orders.flow import order_id_for

FRAME = re.compile(r"f_(\d{5})\.jpg$")
WAIT_LINE = re.compile(r"price wait: (\S+) reason (\S+) candidates (\S+); session (\S+) waits ?(.*)$")
TARGETS = {"order_recall": 0.9, "reference": 0.9, "reference_n": 50, "phone_s": 5, "ask_s": 5, "iph": 10}
DRAFTED = {"confirm_order", "needs_seller", "ask_customer"}  # gold actions that start an order or a question
# the A2 denominator: current / previous with a gold item; unresolved is scored in order_ids
REFERENCES = ("current_card", "previous_card")
MISSED_MS = 60_000
SITUATION_TESTS = {  # situation -> the test files of the "mã đã kiểm" column (pl-report-2026-10-27-2300)
    1: ["tests/test_card.py", "tests/test_card_handoff.py", "tests/test_store_sse.py"],
    2: ["tests/test_price_route.py", "tests/test_price_proposal.py", "tests/test_orders_price_proposal.py"],
    3: ["tests/test_labels.py", "tests/test_measure.py"],
    4: ["tests/test_orders_ask_variant.py", "tests/test_replay_ask_variant.py"],
    5: ["tests/test_labels.py", "tests/test_store_sse.py"],
    6: ["tests/test_card_window.py", "tests/test_price_proposal.py"],
    7: ["tests/test_orders_cancel.py"],
    8: ["tests/test_orders_cancel.py"],
    9: ["tests/test_labels.py", "tests/test_measure.py"],
    10: ["tests/test_orders_ok_after_card_change.py"],
    11: ["tests/test_meta.py", "tests/test_worker.py"],
    12: ["tests/test_orders_flow.py", "tests/test_measure.py"],
}


def counted(row: dict) -> bool:
    """In the references n: a gold order comment that names an item and the current or previous card."""
    o = row.get("order") or {}
    return row["label"] == "order" and bool(o.get("product")) and o.get("reference_kind") in REFERENCES


def spans(windows: dict) -> list[tuple[int, int, str]]:
    """(start, end, item) per item: from its change band, where the camera first sees it, to the next one's."""
    segs, out = windows["segments"], []
    for j, s in enumerate(segs):
        nxt = segs[j + 1] if j + 1 < len(segs) else None
        end = (nxt["band_ms"] or [nxt["from_ms"]])[0] if nxt else windows["loop_ms"]
        if s["product"]:
            out.append(((s["band_ms"] or [s["from_ms"]])[0], end, s["product"]))
    return out


class Run:
    """Indexes over one session's events, the loop clock and the gold."""

    def __init__(self, events: list[dict], loops: list[dict], gold: Gold):
        self.events, self.gold, self.loops = events, gold, loops
        self.clock = Loops([lp["t0_ms"] for lp in loops])
        self.by_id = {e["event_id"]: e for e in events}
        self.cards, self.orders, self.out = defaultdict(list), defaultdict(list), defaultdict(list)
        self.comments, self.labels = {}, {}
        for e in events:
            if e["type"] == "card":
                self.cards[e["card_id"]].append(e)
            elif e["type"] == "order":
                self.orders[e["order_id"]].append(e)
            elif e["type"] == "message_out":
                payload = e["payload"] or {}
                for oid in {payload.get("order_id"), *payload.get("order_ids", [])}:
                    self.out[oid].append(e)
            elif e["type"] == "comment":
                self.comments[e["comment_id"]] = e
            elif e["type"] == "comment_labeled":
                self.labels[e["comment_id"]] = e
        self.item = {cid: self._item(vs) for cid, vs in self.cards.items()}

    def loop(self, ts: int) -> int | None:
        hit = self.clock.of(ts)
        return hit[0] if hit else None

    def _item(self, versions: list[dict]) -> str | None:
        """The item a card stands for: the gold item whose span (from its change band to the next one's) holds the
        frame that opened the card, the shop's rule (c5_shop.Shop.judge). A card that took a price stays live to
        the loop end, so its live span says nothing about its item."""
        first = min(versions, key=lambda c: c["version"])
        frame = first["provenance"].get("frame_ts_ms") or first["ts_ms"]
        hit = self.clock.of(frame)
        return self.gold.card_item(frame - hit[1]) if hit else None

    def version(self, card_id: str, version: int) -> dict | None:
        return next((c for c in self.cards.get(card_id, []) if c["version"] == version), None)

    def card_of(self, cid: str) -> tuple[str | None, str]:
        """(card_id, source) the system tied a comment to: its order line, its ask_variant question, its label."""
        oid = order_id_for(cid)
        if self.orders.get(oid):
            return self.orders[oid][0]["lines"][0]["card_id"], "order"
        ask = next((m for m in self.out.get(oid, []) if m["template"] == "ask_variant"), None)
        if ask:
            return ask["payload"].get("card_id"), "ask_variant"
        draft = (self.labels.get(cid) or {}).get("order_draft") or {}
        return draft.get("referenced_card_id"), "label" if draft.get("referenced_card_id") else "none"

    def label_card(self, cid: str) -> str | None:
        """core.c3's field: the card the comment's latest label drafted."""
        return ((self.labels.get(cid) or {}).get("order_draft") or {}).get("referenced_card_id")

    def why_wrong(self, cid: str, card: str | None, item: str) -> str:
        """Why the drafted card is not the gold item's, from the cards of the comment's loop and their times."""
        if card is None:
            return "no_card"
        at = core.when(self.comments[cid])
        mine = [
            c
            for c, it in self.item.items()
            if it == item and self.loop(self.cards[c][0]["ts_ms"]) == self.loop(at)
        ]
        live = [v["ts_ms"] for c in mine for v in self.cards[c] if v["status"] == "live"]
        if not mine:
            return "no_card_for_item"
        if not live:
            return "item_card_never_live"
        return "item_card_live_after_comment" if min(live) > at else "item_card_live_before_comment"

    def first_reply_ms(self, cid: str) -> int | None:
        """First order event or message for the comment's order: the moment the system took the order up."""
        oid = order_id_for(cid)
        times = [core.when(e) for e in self.orders.get(oid, []) + self.out.get(oid, [])]
        return min(times) if times else None


def references(run: Run, rows: list[dict]) -> dict:
    """core.c3's field, adapted to the C5 gold: the latest label's order_draft.referenced_card_id against the item
    the gold row names. n (the A2 denominator) is every posted order comment whose gold names an item and a current
    or previous card (unresolved is scored in order_ids); a comment with no card is wrong and stays in n. core.c3 is
    not called as-is: its gold holds a card id, the C5 gold an item, so the card -> item map (Run._item) is this
    module's. `by_source` and `disagree` set the label's card beside the one its order or ask_variant question
    carries (Run.card_of); they do not change the score."""
    out = {
        "n": 0,
        "right": 0,
        "no_card": 0,
        "by_kind": {},
        "by_source": Counter(),
        "disagree": [],
        "wrong": [],
    }
    for r in rows:
        o = r.get("order") or {}
        if not counted(r) or r["comment_id"] not in run.comments:
            continue
        card = run.label_card(r["comment_id"])
        used, source = run.card_of(r["comment_id"])
        kind = out["by_kind"].setdefault(o["reference_kind"], {"n": 0, "right": 0})
        out["n"] += 1
        kind["n"] += 1
        out["by_source"][source] += 1
        out["no_card"] += card is None
        if used != card:
            out["disagree"].append(
                {"comment_id": r["comment_id"], "label_card": card, "card": used, "source": source}
            )
        if card is not None and run.item.get(card) == o["product"]:
            out["right"] += 1
            kind["right"] += 1
        else:
            out["wrong"].append({"comment_id": r["comment_id"], "card_id": card, "card_item": run.item.get(card),
                                 "why": run.why_wrong(r["comment_id"], card, o["product"])})  # fmt: skip
    out["accuracy"] = out["right"] / out["n"] if out["n"] else None
    out["gate"] = bool(out["n"] >= TARGETS["reference_n"] and (out["accuracy"] or 0) >= TARGETS["reference"])
    return out


def order_ids(run: Run, rows: list[dict]) -> dict:
    """HQ 02:46: each posted gold order comment by id -> what the system made of it (order, ask_variant, unresolved,
    other), the card its label drafted and the card its order or question carries, right or wrong against the gold
    item, whether it is in the references n, and why a wrong one is wrong. No id is left out."""
    out = []
    for r in rows:
        if r["label"] != "order" or r["comment_id"] not in run.comments:
            continue
        cid, o = r["comment_id"], r.get("order") or {}
        label = run.labels.get(cid) or {}
        draft = label.get("order_draft") or {}
        card = draft.get("referenced_card_id")
        used, source = run.card_of(cid)
        outcome = (
            "order" if run.orders.get(order_id_for(cid)) else "ask_variant" if source == "ask_variant"
            else "unresolved" if draft.get("reference_kind") == "unresolved" else "other"
        )  # fmt: skip
        right = counted(r) and card is not None and run.item.get(card) == o["product"]
        out.append({
            "comment_id": cid, "outcome": outcome, "label": label.get("label"),
            "gold_item": o.get("product"), "gold_kind": o.get("reference_kind"),
            "expected_action": r.get("expected_action"), "draft_kind": draft.get("reference_kind"),
            "label_card": card, "card": used, "card_source": source, "card_item": run.item.get(card),
            "in_n": counted(r), "right": right,
            "why": None if right or not counted(r) else run.why_wrong(cid, card, o["product"]),
        })  # fmt: skip
    return {"n": len(out), "outcomes": dict(Counter(x["outcome"] for x in out)),
            "in_n": sum(x["in_n"] for x in out), "right": sum(x["right"] for x in out),
            "why": dict(Counter(x["why"] for x in out if x["why"])), "rows": out}  # fmt: skip


def proposals(run: Run) -> dict:
    """Addendum 3 §3: how each price proposal closed, which were wrong, and the shop's handling time.

    A proposal closes as D-024 says: at the first version newer than its card_version that is retired or has a
    price other than its current_price (worker versions copy edited_by, so that field does not tell; the proposal's
    ts_ms is the time of its speech, so it does not order the proposal among the card's versions).

    HQ 04:18: a proposal whose closing version was ingested before it is stale on arrival; the shop had no time to
    act on it. Its raw handling time stays in its row and in `handling_ms_all`, and `handling_ms` is the median of
    the others. A wrong proposal is counted under `card` when its card is not the held item, else under `price`."""
    props = [e for e in run.events if e["type"] == "price_proposal"]
    ids = {p["event_id"] for p in props}
    rows = []
    for p in props:
        later = [c for c in run.cards[p["card_id"]] if c["version"] > p["card_version"]]
        outcome, closed = "dropped", None
        for c in later:
            if c["status"] == "retired":
                outcome, closed = "dropped", c
                break
            if c["price"] == p["current_price"]:
                continue
            refs = set(c.get("caused_by") or []) & ids
            outcome = (
                "used" if p["event_id"] in refs else "replaced" if refs
                else "fixed" if c["price"] != p["heard_price"] else "other"
            )  # fmt: skip
            closed = c
            break
        start, end = run.clock.span(run.by_id.get(p["speech_event_id"]))
        held = run.gold.held_at(start) if start is not None else None
        right_price = held is not None and run.gold.right_price(held, p["heard_price"], start, end)
        right_card = held is not None and run.item.get(p["card_id"]) == held
        took = core.when(closed) - core.when(p) if closed else None
        rows.append({
            "event_id": p["event_id"], "loop": run.loop(p["ts_ms"]), "card_id": p["card_id"],
            "had_price": p.get("current_price") is not None, "outcome": outcome,
            "closed_by": closed["event_id"] if closed else None, "right_card": right_card,
            "right_price": right_price, "wrong": not (right_card and right_price),
            "stale": took is not None and took < 0,
            "handling_ms": took if outcome in ("used", "fixed") else None,
        })  # fmt: skip
    wrong = [r for r in rows if r["wrong"]]
    timed = [r for r in rows if r["handling_ms"] is not None]
    acted = [r["handling_ms"] for r in timed if not r["stale"]]
    return {
        "n": len(rows),
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "right_card": sum(r["right_card"] for r in rows),
        "right_price": sum(r["right_price"] for r in rows),
        "both": sum(r["right_card"] and r["right_price"] for r in rows),
        "wrong_card": sum(not r["right_card"] for r in rows),
        "wrong": {"n": len(wrong), "outcomes": dict(Counter(r["outcome"] for r in wrong)),
                  "card": sum(not r["right_card"] for r in wrong), "price": sum(r["right_card"] for r in wrong)},
        "stale": {"n": sum(r["stale"] for r in rows), "ids": [r["event_id"] for r in rows if r["stale"]],
                  "reason": "closed by a version ingested before the proposal"},
        "handling_ms": {"median": median(acted) if acted else None, "n": len(acted)},
        "handling_ms_all": {"median": median([r["handling_ms"] for r in timed]) if timed else None, "n": len(timed)},
        "rows": rows,
    }  # fmt: skip


def needs_seller(run: Run, catalog: list[dict]) -> dict:
    """Entries into NEEDS_SELLER by cause, and HQ423 (4): would-be price candidates, offline, per loop."""
    prices = Counter(i["price"] for i in catalog)
    loops = defaultdict(
        lambda: {"entries": 0, "causes": Counter(), "orders": 0, "would_be": 0, "sku_none": 0}
    )
    raw = Counter()
    for evs in run.orders.values():
        entries = [e for e in evs if e["state"] == "NEEDS_SELLER" and e.get("prev_state") != "NEEDS_SELLER"]
        for e in entries:
            row = loops[run.loop(e["ts_ms"])]
            row["entries"] += 1
            row["causes"][cause(e.get("reason"))] += 1
            raw[re.sub(r"\d[\d.,]*", "#", e.get("reason") or "")] += 1  # amounts out, the rule text stays
        if entries:
            first = entries[0]
            line = first["lines"][0]
            card = run.version(line["card_id"], line["card_version"]) or {}
            row = loops[run.loop(first["ts_ms"])]
            row["orders"] += 1
            row["would_be"] += card.get("price") is not None and prices[card["price"]] == 1
            row["sku_none"] += line.get("sku") is None
    total = {"entries": 0, "causes": Counter(), "orders": 0, "would_be": 0, "sku_none": 0}
    for row in loops.values():
        for key in total:
            total[key] += row[key]
    fmt = lambda r: r | {"causes": dict(r["causes"])}  # noqa: E731
    return {"per_loop": {k: fmt(v) for k, v in sorted(loops.items(), key=lambda kv: kv[0] or 0)},
            "total": fmt(total), "reasons": dict(raw)}  # fmt: skip


def seller_price(run: Run, e: dict) -> bool:
    """A card version the seller made that changes the price: edited_by "seller" and a price other than the
    version before it. Worker and retire versions copy edited_by (DECISIONS) at the same price, so they do not."""
    if e["type"] != "card" or e["provenance"].get("edited_by") != "seller":
        return False
    before = run.version(e["card_id"], e["version"] - 1)
    return before is None or before.get("price") != e.get("price")


def intervention(run: Run, e: dict) -> bool:
    return seller_price(run, e) or (e["type"] == "order" and e["actor"] == "seller")


# a seller version's cause: the field groups it changes against the version before it
CARD_FIELDS = (("price", ("price",)), ("item", ("sku", "name")), ("stock", ("stock", "variants")))
# a seller order's cause: c5_shop.cause of the NEEDS_SELLER reason before it
ORDER_CAUSE = {"no_price": "price", "price_changed": "price", "discount": "price", "amount": "price",
               "over_stock": "stock", "variant": "stock", "card_state": "item"}  # fmt: skip


def intervention_row(run: Run, e: dict, kind: str, why: list[str], proposals: list[dict]) -> dict:
    card_id = e["card_id"] if kind == "card" else e["lines"][0]["card_id"]
    before = sorted(
        (p for p in proposals if p["card_id"] == card_id and core.when(p) <= core.when(e)), key=core.when
    )
    took = core.when(e) - core.when(before[-1]) if before else None
    return {
        "event_id": e["event_id"],
        "loop": run.loop(e["ts_ms"]),
        "kind": kind,
        "took_ms": took,
        "cause": why,
    }


def interventions(run: Run, c5: dict) -> dict:
    """The C5 interventions (HQ 03:48): a seller price version (seller_price) or a seller order decision, with the
    breakdown by cause summing to the total. core.c5 counts the same (a seller version that changes the price, not
    its copies); its number stays beside this one as `core_c5`. The 10 / h threshold is unchanged. System
    questions to the customer are listed beside the total, not in it.

    HQ 06:58 (e): `rows` holds one row per intervention (as many as `total`): event id, loop, kind (card / order),
    took_ms and cause. took_ms is the raw core.when(e) - core.when(p), p the last price proposal of the same card
    (an order's: its line's card) with core.when(p) <= core.when(e); None when there is none. cause is a list:

        card version, against version - 1 (a first version against an empty one):
            price changed -> "price"; sku or name -> "item"; stock or variants -> "stock"
        order, c5_shop.cause of the last NEEDS_SELLER reason before it:
            no_price, price_changed, discount, amount -> "price"
            over_stock, variant -> "stock"
            card_state -> "item"
        anything else (other, no NEEDS_SELLER before, a version that changes none of these) -> "error"
    """
    props = [e for e in run.events if e["type"] == "price_proposal"]
    ids = {p["event_id"] for p in props}
    parts, rows = Counter(), []
    for e in run.events:
        if seller_price(run, e):
            parts["price_used" if set(e.get("caused_by") or []) & ids else "price_typed"] += 1
            last = run.version(e["card_id"], e["version"] - 1) or {}
            why = [name for name, keys in CARD_FIELDS if any(e.get(k) != last.get(k) for k in keys)]
            rows.append(intervention_row(run, e, "card", why or ["error"], props))
        elif e["type"] == "order" and e["actor"] == "seller":
            before = [
                o
                for o in run.orders[e["order_id"]]
                if o["state"] == "NEEDS_SELLER" and o["ts_ms"] <= e["ts_ms"]
            ]
            why = cause(before[-1].get("reason")) if before else "no_needs_seller"
            parts[f"order:{why}->{e['state']}"] += 1
            rows.append(intervention_row(run, e, "order", [ORDER_CAUSE.get(why, "error")], props))
    total = sum(parts.values())
    hours = (
        (max(map(core.when, run.events)) - min(map(core.when, run.events))) / 3_600_000 if run.events else 0
    )
    asks = sum(e["type"] == "message_out" and e["template"] == "ask_variant" for e in run.events)
    return {"total": total, "per_hour": total / hours if hours else None, "by_cause": dict(parts),
            "core_c5": c5["interventions"], "ask_customer_messages": asks, "rows": rows}  # fmt: skip


def missed(run: Run, rows: list[dict]) -> dict:
    """An order comment the system did not take up (an order event or a message for its order) within 60 s."""
    out = {"n": 0, "missed": [], "no_product": 0}
    for r in rows:
        if (
            r["label"] != "order"
            or r.get("expected_action") not in DRAFTED
            or r["comment_id"] not in run.comments
        ):
            continue
        if not r["order"].get("product"):
            out["no_product"] += 1
            continue
        out["n"] += 1
        first = run.first_reply_ms(r["comment_id"])
        if first is None or first - core.when(run.comments[r["comment_id"]]) > MISSED_MS:
            out["missed"].append(r["comment_id"])
    return out | {"missed_n": len(out["missed"])}


def receipts(run: Run) -> dict:
    """Each CONFIRMED order: its receipt names a card version that exists, at that version's price, and the
    version the customer was asked to confirm. The image is counted apart (the C5 catalog has none)."""
    rows = []
    for oid, evs in run.orders.items():
        for e in (e for e in evs if e["state"] == "CONFIRMED"):
            r = e.get("receipt") or {}
            r = {**r, "card_id": r.get("card_id") or e["lines"][0]["card_id"]}
            card = run.version(r["card_id"], r.get("card_version"))
            asked = [
                m
                for m in run.out.get(oid, [])
                if m["template"] == "confirm_order" and m["ts_ms"] <= e["ts_ms"]
            ]
            rows.append({
                "event_id": e["event_id"], "order_id": oid,
                "version_ok": bool(card and r.get("price") == card["price"]
                                   and asked and asked[-1]["payload"]["card_version"] == r.get("card_version")),
                "image": bool(r.get("image_url")),
                "image_source": image_source(run, r),
            })  # fmt: skip
    ok = sum(r["version_ok"] for r in rows)
    return {"n": len(rows), "version_ok": ok, "rate": ok / len(rows) if rows else None,
            "with_image": sum(r["image"] for r in rows),
            "image_path_run": sum(r["image_source"]["image_path"] == "RUN" for r in rows), "rows": rows}  # fmt: skip


def first_cause(event: dict | None) -> str | None:
    return next(iter((event or {}).get("caused_by") or []), None)


def image_source(run: Run, receipt: dict) -> dict:
    """Where a receipt's image comes from: the card, the product_seen that opened it (its first version's cause),
    that product_seen's frame (its cause, at its frame_ts_ms) and the frame's source file, then the cut-out the
    worker logged for that source frame whose png has the image's sha256 (the run's loops, "cutouts"). Any field
    missing: the image path is NOT_RUN. A paid run's image is a model output of the run, not joined here."""
    cid = receipt.get("card_id")
    first = min(run.cards.get(cid, []), key=lambda c: c["version"], default=None)
    seen = run.by_id.get(first_cause(first))
    seen = seen if seen and seen["type"] == "product_seen" else None
    frame = run.by_id.get(first_cause(seen))
    at = frame if frame and seen and frame.get("ts_ms") == seen.get("frame_ts_ms") else None
    m = FRAME.search(str((at or {}).get("jpeg_path") or ""))
    i = int(m.group(1)) if m else None
    url = str(receipt.get("image_url") or "")
    sha = (
        hashlib.sha256(base64.b64decode(url.split(",", 1)[1])).hexdigest()
        if url.startswith("data:")
        else None
    )
    cut = next((c for lp in run.loops for c in lp.get("cutouts", [])
                if sha and c.get("png_sha256") == sha and i is not None and c.get("frame") == i), None)  # fmt: skip
    out = {"card_id": cid, "card_version": receipt.get("card_version"),
           "product_seen": seen and seen["event_id"], "frame_ts_ms": seen and seen.get("frame_ts_ms"),
           "frame_event": at and at["event_id"], "source_frame": i, "image_sha256": sha,
           "source_session": cut and cut.get("source_session"), "source_t": cut and cut.get("source_t")}  # fmt: skip
    missing = [k for k, v in out.items() if v is None]
    return {**out, "image_path": "NOT_RUN" if missing else "RUN", "missing": missing}


def waits(loops: list[dict]) -> dict:
    """HQ419: one wait per (loop, reason, line event id) from the loop's log lines, checked against the loop-end
    snapshot and the last line's suffix; the total is the sum of the loops, never the session's last line."""
    per, total = {}, Counter()
    for lp in loops:
        seen, suffix = set(), {}
        for text in lp.get("wait_lines", []):
            m = WAIT_LINE.search(text)
            if not m:
                continue
            seen.add((m[2], m[1]))
            suffix = dict(p.split("=") for p in m[5].split()) if m[5] else {}
        counted = Counter(reason for reason, _ in seen)
        snap = {r: int(n) for r, n in (lp.get("waits") or {}).items()}
        per[lp["k"]] = {"lines": dict(counted), "snapshot": snap, "suffix": {r: int(n) for r, n in suffix.items()},
                        "agree": dict(counted) == snap == {r: int(n) for r, n in suffix.items()}}  # fmt: skip
        total += counted
    return {"per_loop": per, "total": dict(total), "all_agree": all(v["agree"] for v in per.values())}


def verdict(cases: list[tuple[str, str | None]], note: str = "") -> dict:
    hit = [c for c in cases if c[0] != "not_triggered"]
    bad = [c for c in hit if c[0] == "fail"]
    status = "NOT_TRIGGERED" if not hit else "FAIL" if bad else "PASS"
    eid = (bad or hit or [(None, None)])[0][1]
    return {
        "status": status,
        "triggered": len(hit),
        "passed": len(hit) - len(bad),
        "event_id": eid,
        "note": note,
    }
