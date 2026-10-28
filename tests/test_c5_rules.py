"""The C5 rules HQ ruled on at 03:48, on synthetic events and the committed C5 gold (windows.json):

- interventions: a seller version that changes the price, or a seller order decision (core.c5 counts the same);
- situation 2: a wrong proposal fails only when it is used; a right one passes when used, fixed or replaced;
  outcome "other" is reported beside the verdict, not scored;
- right price: the gold price at the end of the line, or either gold price when the line overlaps the window
  where the host says the change; the shop and the scorer apply the same rule.
"""

import json

from app.settings import ROOT
from measure import c5_score, core
from measure.c5_report import situations
from measure.c5_score import Run
from measure.c5_shop import Feed, Gold, Loops, Shop

T0 = 1_000_000
LOOPS = [{"k": 1, "t0_ms": T0, "end_ms": T0 + 351_500}]
WINDOWS = json.loads((ROOT / "data" / "comments" / "c5" / "windows.json").read_text())
PRICES = {"SP01": {"price_first": 250_000, "price_fixed": 220_000},
          "SP04": {"price_first": 180_000, "price_fixed": 160_000}}  # fmt: skip
GOLD = Gold(WINDOWS, PRICES)


def card(cid, version, status, ts, price, edited_by=None, frame=None):
    provenance = {"edited_by": edited_by} | ({} if frame is None else {"frame_ts_ms": T0 + frame})
    return {"type": "card", "event_id": f"{cid}-v{version}", "card_id": cid, "version": version, "status": status,
            "ts_ms": T0 + ts, "price": price, "sku": None, "caused_by": [], "provenance": provenance}  # fmt: skip


def order(oid, ts, state, actor, reason=None):
    return {"type": "order", "event_id": f"{oid}-{state}", "order_id": oid, "ts_ms": T0 + ts, "state": state,
            "prev_state": None, "actor": actor, "reason": reason,
            "lines": [{"card_id": "card-a", "card_version": 2, "sku": None}]}  # fmt: skip


def speech(eid, start, end):
    return {
        "type": "speech",
        "event_id": f"s-{eid}",
        "ts_ms": T0 + start,
        "start_ms": T0 + start,
        "end_ms": T0 + end,
    }


def proposal(eid, start, card_id, current, heard):
    return {"type": "price_proposal", "event_id": eid, "card_id": card_id, "card_version": 2,
            "current_price": current, "heard_price": heard, "speech_event_id": f"s-{eid}",
            "ts_ms": T0 + start, "ingested_ms": T0 + start + 4_200}  # fmt: skip


def test_interventions_count_a_seller_price_change_not_its_copies():
    events = [
        card("card-a", 1, "draft", 16_000, None, frame=15_000),
        card("card-a", 2, "live", 26_000, 250_000),
        card("card-a", 3, "edited", 30_000, 220_000, "seller"),  # the seller types a price
        card("card-a", 4, "live", 31_000, 220_000, "seller"),  # a worker version copies edited_by
        card(
            "card-a", 5, "retired", 60_000, 220_000, "seller"
        ),  # a retire that copied it (before the runner fix)
        order("o-1", 40_000, "NEEDS_SELLER", "system", "qty 5 > stock 3"),
        order("o-1", 44_000, "CANCELLED", "seller"),
    ]
    run = Run(events, LOOPS, GOLD)
    got = c5_score.interventions(run, core.c5(events, {"comments": {}}))
    assert got["total"] == 2
    assert got["core_c5"] == got["total"]  # core.c5 counts the same: the price the seller typed and the order
    assert got["by_cause"] == {"price_typed": 1, "order:over_stock->CANCELLED": 1}
    assert got["per_hour"] == 2 / ((60_000 - 16_000) / 3_600_000)
    assert sum(c5_score.intervention(run, e) for e in events) == 2  # the per-loop count uses the same rule


def test_core_c5_counts_a_seller_version_only_when_it_changes_the_price():
    events = [
        card("card-b", 1, "draft", 1_000, 180_000, "seller"),  # the card's first version, made by the seller
        card("card-b", 2, "live", 2_000, 180_000, "seller"),  # a copy at the price before it
        card("card-b", 3, "edited", 3_000, 160_000, "seller"),  # the seller changes the price
    ]
    counts = [core.c5(events[:n], {"comments": {}})["interventions"] for n in (1, 2, 3)]
    assert counts == [1, 1, 2]


def test_situation_2_fails_a_wrong_proposal_only_when_it_is_used():
    rows = [
        {"event_id": eid, "had_price": had, "outcome": outcome, "wrong": wrong, "closed_by": None}
        for eid, had, outcome, wrong in [
            ("p-a", True, "replaced", True),  # blocked: pass
            ("p-b", True, "dropped", True),  # blocked: pass
            ("p-c", True, "used", True),  # a wrong price on the card: fail
            ("p-d", True, "replaced", False),  # pass
            ("p-e", True, "used", False),  # pass
            ("p-f", True, "other", True),  # reported beside the verdict, not scored
            ("p-g", True, "fixed", True),  # blocked, the seller typed a price: pass
            ("p-h", True, "dropped", False),  # a right price lost: fail
            ("p-i", False, "used", True),  # no price on the card yet: not situation 2
        ]
    ]
    parts = {"proposals": {"rows": rows}, "waits_lines": [], "receipts": {"rows": []},
             "references": {"n": 0, "by_kind": {}, "gate": False, "right": 0, "wrong": [], "accuracy": None,
                            "no_card": 0}}  # fmt: skip
    s2 = situations(Run([], LOOPS, GOLD), [], [], {}, parts, {})[2]
    assert (s2["status"], s2["triggered"], s2["passed"], s2["event_id"]) == ("FAIL", 7, 5, "p-c")
    assert s2["other"] == ["p-f"]


def boundary_events():
    """SP04's price is said at [152900, 159300]: one line ends inside the window, one ends before it."""
    return [
        card("card-4", 1, "draft", 126_500, None, frame=125_000),
        card("card-4", 2, "live", 131_000, 180_000),
        speech("p-in", 152_860, 155_260),
        proposal("p-in", 152_860, "card-4", 180_000, 160_000),
        speech("p-before", 148_000, 152_000),
        proposal("p-before", 148_000, "card-4", 180_000, 160_000),
    ]


def test_a_line_that_runs_into_the_window_hears_a_right_price():
    rows = {r["event_id"]: r for r in c5_score.proposals(Run(boundary_events(), LOOPS, GOLD))["rows"]}
    assert (rows["p-in"]["right_card"], rows["p-in"]["wrong"]) == (True, False)
    assert rows["p-before"]["wrong"] is True


def test_the_shop_accepts_exactly_the_proposals_the_scorer_calls_right():
    returning = [  # SP01 comes back at the loop end at its fixed price; the line says the first price
        card("card-1", 1, "draft", 331_500, None, frame=330_000),
        card("card-1", 2, "live", 333_000, 220_000),
        speech("p-old", 334_860, 336_000),
        proposal("p-old", 334_860, "card-1", 220_000, 250_000),
    ]
    events = boundary_events() + returning
    feed = Feed()
    for seq, ev in enumerate(events, 1):
        feed.add(seq, ev)
    shop = Shop(http=None, sid="s", gold=GOLD, feed=feed, loops=Loops([T0]))
    rows = c5_score.proposals(Run(events, LOOPS, GOLD))["rows"]
    actions = {
        r["event_id"]: shop.judge(next(e for e in events if e["event_id"] == r["event_id"]))[0] for r in rows
    }
    assert actions == {
        "p-in": "accept",
        "p-before": "drop",
        "p-old": "drop",
    }  # p-before: the card holds the price
    assert all((actions[r["event_id"]] == "accept") == (not r["wrong"]) for r in rows)
