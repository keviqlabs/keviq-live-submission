"""C5 report: the 12 situations x 2 columns, the per-loop table, the with/without-Keviq estimate, and score(), which
puts every number of measure/c5_score.py together for one run file. Pure, like c5_score."""

from collections import Counter

from measure import core
from measure.c5_score import (
    SITUATION_TESTS,
    TARGETS,
    WAIT_LINE,
    Run,
    intervention,
    interventions,
    missed,
    needs_seller,
    order_ids,
    proposals,
    receipts,
    references,
    spans,
    verdict,
    waits,
)
from orders.flow import order_id_for

ASSUMED = {  # the "không Keviq" column: assumptions, not measurements
    "manual_s_per_order": 60,  # the shop reads an order comment, asks what is missing, writes the order down
    "manual_s_per_question": 20,  # the shop answers a size/stock question by hand
    "tap_s": 4,  # one shop tap in the Keviq console when no handling time was measured (the runner's delay)
}


def situations(run: Run, rows: list[dict], msgs: list[dict], sent: dict, parts: dict, code_rc: dict) -> dict:
    """PASS / FAIL / NOT_TRIGGERED per situation with an event id ("trải nghiệm đã chạy"), beside the rc of the
    situation's test files ("mã đã kiểm"). `sent`: msg_id -> customer log row; `parts`: the score parts."""
    posted = [r for r in rows if r["comment_id"] in run.comments]
    eid = lambda cid: run.comments[cid]["event_id"]  # noqa: E731
    states = lambda oid: {e["state"] for e in run.orders.get(oid, [])}  # noqa: E731
    out = {}

    items = sorted({s[2] for s in spans(run.gold.windows)})
    cases = []
    for lp in run.loops:
        for item in items:
            card = next(
                (
                    c
                    for c, it in run.item.items()
                    if it == item and run.loop(run.cards[c][0]["ts_ms"]) == lp["k"]
                ),
                None,
            )
            cases.append(("pass", run.cards[card][0]["event_id"]) if card else ("fail", None))
    out[1] = verdict(cases, f"items with a card per loop out of {len(items)}")

    cases, other = [], []  # ruling 03:48: a wrong proposal fails only when it is used; "other" is not scored
    for p in (p for p in parts["proposals"]["rows"] if p["had_price"]):
        if p["outcome"] == "other":
            other.append(p["event_id"])
            continue
        ok = p["outcome"] != "used" if p["wrong"] else p["outcome"] in ("used", "fixed", "replaced")
        cases.append(("pass" if ok else "fail", p["closed_by"] or p["event_id"]))
    out[2] = verdict(cases, "proposals on a card that already had a price") | {"other": other}

    ref = parts["references"]
    prev = ref["by_kind"].get("previous_card", {"n": 0, "right": 0})
    out[3] = {
        "status": "NOT_TRIGGERED" if not ref["n"] else "PASS" if ref["gate"] else "FAIL",
        "triggered": ref["n"], "passed": ref["right"],
        "event_id": eid(ref["wrong"][0]["comment_id"]) if ref["wrong"] else None,
        "note": f"accuracy {ref['accuracy']} on n={ref['n']} (target {TARGETS['reference']} on >= "
                f"{TARGETS['reference_n']}); previous_card {prev['right']}/{prev['n']}; no card {ref['no_card']}",
    }  # fmt: skip

    cases = []
    for r in (r for r in posted if r.get("expected_action") == "ask_customer" and r["order"].get("product")):
        ask = next(
            (m for m in run.out.get(order_id_for(r["comment_id"]), []) if m["template"] == "ask_variant"),
            None,
        )
        late = ask and core.when(ask) - core.when(run.comments[r["comment_id"]]) > TARGETS["ask_s"] * 1000
        cases.append(("fail", eid(r["comment_id"])) if not ask or late else ("pass", ask["event_id"]))
    stock = [r for r in posted if r["label"] == "stock_question"]
    answered = sum(any((m["payload"] or {}).get("comment_id") == r["comment_id"] for m in run.events
                       if m["type"] == "message_out") for r in stock)  # fmt: skip
    out[4] = verdict(
        cases, f"ask_variant <= {TARGETS['ask_s']} s; stock_question answered {answered}/{len(stock)}"
    )

    cases = []
    for r in (r for r in posted if "arrives_ms" in r and 5 in r["situations"]):
        card, _ = run.card_of(r["comment_id"])
        cases.append(
            ("pass" if card and run.item.get(card) == r["order"]["product"] else "fail", eid(r["comment_id"]))
        )
    out[5] = verdict(cases, "late comments tied to the card shown when they were written")

    cases = [("pass", eid_) for loop in parts["waits_lines"] for _, eid_ in loop]
    out[6] = verdict(cases, "a price wait happened (lines per loop, deduped); existence only")

    cases = []
    for r in (r for r in posted if r["label"] == "other" and 7 in r["situations"]):
        mine = [
            o
            for o in posted
            if o["label"] == "order" and 7 in o["situations"] and o["user_ref"] == r["user_ref"]
        ]
        oid = order_id_for(mine[0]["comment_id"]) if mine else None
        if not oid or not run.orders.get(oid):
            cases.append(("not_triggered", eid(r["comment_id"])))
        else:
            cases.append(("fail" if "CANCELLED" in states(oid) else "pass", eid(r["comment_id"])))
    out[7] = verdict(cases, "a 'do not cancel' comment leaves the order open")

    def by_messenger(situation: int, ok, changed=None) -> list:
        cases = []
        for m in (m for m in msgs if m["situation"] == situation):
            oid, log = order_id_for(m["order_comment_id"]), sent.get(m["msg_id"]) or {}
            if ok(oid):
                cases.append(("pass", run.orders[oid][-1]["event_id"]))
            elif not log.get("sent") or (changed and not changed(oid, log)):
                cases.append(("not_triggered", log.get("message_in")))
            else:
                cases.append(("fail", log.get("message_in")))
        return cases

    out[8] = verdict(
        by_messenger(8, lambda oid: "CANCELLED" in states(oid)), "CANCEL quick reply -> CANCELLED"
    )

    hidden = {}
    for e in run.events:
        if e["type"] == "comment_labeled" and e.get("action") == "hide":
            hidden.setdefault(e["comment_id"], core.when(e))
    cases = []
    for r in (r for r in posted if r["has_phone"] and 9 in r["situations"]):
        s = (
            (hidden[r["comment_id"]] - core.when(run.comments[r["comment_id"]])) / 1000
            if r["comment_id"] in hidden
            else None
        )
        cases.append(("pass" if s is not None and s <= TARGETS["phone_s"] else "fail", eid(r["comment_id"])))
    out[9] = verdict(cases, f"spelled-out phone hidden within {TARGETS['phone_s']} s")

    def again(oid):
        return any(
            e["state"] == "NEEDS_SELLER" and "confirms again" in (e.get("reason") or "")
            for e in run.orders.get(oid, [])
        )

    def changed(oid, log):  # a newer version of the order's card came out before the customer's "ok"
        line = (run.orders.get(oid) or [{}])[0].get("lines", [{}])[0]
        return any(c["version"] > line.get("card_version", 0) and c["ts_ms"] <= log.get("sent_ms", 0)
                   for c in run.cards.get(line.get("card_id"), []))  # fmt: skip

    out[10] = verdict(
        by_messenger(10, again, changed), "'ok' after the card changed -> NEEDS_SELLER, confirm again"
    )
    out[11] = {
        "status": "TEST_ONLY",
        "triggered": 0,
        "passed": 0,
        "event_id": None,
        "note": "schema / 429: tests only",
    }
    good = {r["order_id"] for r in parts["receipts"]["rows"] if r["version_ok"]}
    out[12] = verdict(
        by_messenger(12, lambda oid: oid in good), "CONFIRMED with the receipt at the confirmed version"
    )

    for n, v in out.items():
        rc = (code_rc or {}).get(n)
        v["code"] = {
            "files": SITUATION_TESTS[n],
            "status": "NOT_RUN" if rc is None else "PASS" if rc == 0 else "FAIL",
        }
    return out


def per_loop(run: Run, rows: list[dict], parts: dict) -> list[dict]:
    items = {s[2] for s in spans(run.gold.windows)}
    missed_ids = set(parts["missed"]["missed"])
    out = []
    for lp in run.loops:
        k = lp["k"]
        in_k = lambda e, k=k: run.loop(e["ts_ms"]) == k  # noqa: E731
        firsts = [evs[0] for evs in run.orders.values() if in_k(evs[0])]
        mine = {e["order_id"] for e in firsts}
        props = [p for p in parts["proposals"]["rows"] if p["loop"] == k]
        out.append({
            "loop": k, "t0_ms": lp["t0_ms"], "end_ms": lp["end_ms"],
            "comments": sum(in_k(c) for c in run.comments.values()),
            "orders": len(firsts),
            "confirmed": sum(any(e["state"] == "CONFIRMED" for e in run.orders[o]) for o in mine),
            "cancelled": sum(any(e["state"] == "CANCELLED" for e in run.orders[o]) for o in mine),
            "needs_seller": parts["needs_seller"]["per_loop"].get(k, {}).get("entries", 0),
            "would_be": parts["needs_seller"]["per_loop"].get(k, {}).get("would_be", 0),
            "interventions": sum(in_k(e) and intervention(run, e) for e in run.events),
            "proposals": len(props),
            "right_card": sum(p["right_card"] for p in props),
            "right_price": sum(p["right_price"] for p in props),
            "both": sum(p["right_card"] and p["right_price"] for p in props),
            "waits": sum(parts["waits"]["per_loop"].get(k, {}).get("lines", {}).values()),
            "missed": sum(run.loop(run.comments[c]["ts_ms"]) == k for c in missed_ids),
            "items_with_card": len({run.item[c] for c in run.cards if run.loop(run.cards[c][0]["ts_ms"]) == k} & items),
            "items": len(items),
            "stopped": lp.get("stopped"),
        })  # fmt: skip
    return out


def text(scored: dict) -> list[str]:
    """Ruling 06:58 (e), as printed lines: right_card | right_price | both per loop and in total, then one line per
    intervention (event id, loop, took_ms, cause). Numbers and event ids only."""
    p, iv = scored["proposals"], scored["interventions"]
    out = ["loop | proposals | right_card | right_price | both"]
    out += [f"{lp['loop']} | {lp['proposals']} | {lp['right_card']} | {lp['right_price']} | {lp['both']}"
            for lp in scored["per_loop"]]  # fmt: skip
    out.append(f"total | {p['n']} | {p['right_card']} | {p['right_price']} | {p['both']}")
    out.append(f"interventions {iv['total']}")
    out.append("event_id | loop | took_ms | cause")
    out += [f"{r['event_id']} | {r['loop']} | {r['took_ms']} | {','.join(r['cause'])}" for r in iv["rows"]]
    return out


def estimate(rows: list[dict], run: Run, parts: dict) -> dict:
    """With / without Keviq: "ước tính theo giả định". Only the Keviq interventions are measured."""
    posted = [r for r in rows if r["comment_id"] in run.comments]
    orders = sum(r["label"] == "order" for r in posted)
    questions = sum(r["label"] == "stock_question" for r in posted)
    n = parts["interventions"]["total"]
    tap = parts["proposals"]["handling_ms"]["median"]
    tap_s = tap / 1000 if tap is not None else ASSUMED["tap_s"]
    return {
        "label": "ước tính theo giả định",
        "assumptions": ASSUMED | {"tap_s_used": tap_s, "tap_s_source": "assumed" if tap is None else "measured median"},
        "rows": [
            {"what": "shop actions", "without": orders + questions, "with": n},
            {"what": "shop minutes",
             "without": round((orders * ASSUMED["manual_s_per_order"] + questions * ASSUMED["manual_s_per_question"]) / 60, 1),
             "with": round(n * tap_s / 60, 1)},
            {"what": "orders not taken up in 60 s", "without": "không ước tính", "with": parts["missed"]["missed_n"]},
        ],
    }  # fmt: skip


def score(events: list[dict], run_file: dict, rows: list[dict], msgs: list[dict], gold, catalog: list[dict],
          code_rc: dict | None = None) -> dict:  # fmt: skip
    run = Run(events, run_file["loops"], gold)
    posted = [r for r in rows if r["comment_id"] in run.comments]
    c5 = core.c5(
        events,
        {"comments": {r["comment_id"]: {"label": r["label"], "has_phone": r["has_phone"]} for r in posted}},
    )
    parts = {
        "references": references(run, rows),
        "order_ids": order_ids(run, rows),
        "proposals": proposals(run),
        "needs_seller": needs_seller(run, catalog),
        "missed": missed(run, rows),
        "receipts": receipts(run),
        "waits": waits(run_file["loops"]),
    }
    parts["interventions"] = interventions(run, c5)
    parts["waits_lines"] = [  # one case per (loop, reason, line event id), as ruling 419 counts them
        sorted({(m[2], m[1]) for m in map(WAIT_LINE.search, lp.get("wait_lines", [])) if m})
        for lp in run_file["loops"]
    ]
    sent = {r["msg_id"]: r for r in run_file.get("customers", [])}
    out = {
        "run": run_file.get("run"), "session_id": run_file.get("session_id"), "sha": run_file.get("sha"),
        "config": run_file.get("config"), "loops_run": len(run_file["loops"]),
        "posted": {"comments": len(posted), "messenger_sent": sum(r.get("sent", False) for r in sent.values())},
        "c5": c5, "latency": core.latency(events), "cards": dict(Counter(run.item.values())),
        "card_sku": {c: vs[0]["sku"] for c, vs in run.cards.items()},
        **{k: v for k, v in parts.items() if k != "waits_lines"},
    }  # fmt: skip
    out["situations"] = situations(run, rows, msgs, sent, parts, code_rc or {})
    out["per_loop"] = per_loop(run, rows, parts)
    out["estimate"] = estimate(rows, run, parts)
    return out
