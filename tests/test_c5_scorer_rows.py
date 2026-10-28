"""HQ 06:58 (e), on synthetic events and the committed C5 gold (windows.json):

- the proposal totals count right_price and both (right_card and right_price) beside right_card, total and per loop;
- each C5 intervention is a row: event id, loop, kind, took_ms from the last price proposal of its card that
  arrived before it (None when there is none, raw otherwise), and its cause (price / item / stock / error).
"""

import json

import pytest

from app.settings import ROOT
from measure import c5_report, c5_score, core
from measure.c5_score import Run
from measure.c5_shop import Gold

T0 = 1_000_000
LOOPS = [{"k": 1, "t0_ms": T0, "end_ms": T0 + 351_500}]
WINDOWS = json.loads((ROOT / "data" / "comments" / "c5" / "windows.json").read_text())
PRICES = {"SP01": {"price_first": 250_000, "price_fixed": 220_000},
          "SP04": {"price_first": 180_000, "price_fixed": 160_000}}  # fmt: skip
GOLD = Gold(WINDOWS, PRICES)


def card(cid, version, ts, price, edited_by=None, frame=None, arrived=None, **fields):
    provenance = {"edited_by": edited_by} | ({} if frame is None else {"frame_ts_ms": T0 + frame})
    return {"type": "card", "event_id": f"{cid}-v{version}", "card_id": cid, "version": version, "status": "live",
            "ts_ms": T0 + ts, "price": price, "sku": None, "name": "item", "stock": 5, "variants": [],
            "caused_by": [], "provenance": provenance, **({} if arrived is None else {"ingested_ms": T0 + arrived}),
            **fields}  # fmt: skip


def said(eid, start, end, card_id, current, heard, arrived):
    """The speech line and the proposal made from it; the proposal's ts_ms is the speech's (D-024)."""
    return [
        {"type": "speech", "event_id": f"s-{eid}", "ts_ms": T0 + start, "start_ms": T0 + start, "end_ms": T0 + end},
        {"type": "price_proposal", "event_id": eid, "card_id": card_id, "card_version": 2, "current_price": current,
         "heard_price": heard, "speech_event_id": f"s-{eid}", "ts_ms": T0 + start, "ingested_ms": T0 + arrived},
    ]  # fmt: skip


def order(oid, ts, state, actor, reason=None, card_id="card-1"):
    return {"type": "order", "event_id": f"{oid}-{state}", "order_id": oid, "ts_ms": T0 + ts, "state": state,
            "prev_state": None, "actor": actor, "reason": reason,
            "lines": [{"card_id": card_id, "card_version": 2, "sku": None}]}  # fmt: skip


SP01 = [card("card-1", 1, 16_000, None, frame=15_000), card("card-1", 2, 26_000, 250_000)]
SP04 = [card("card-4", 1, 126_500, None, frame=125_000), card("card-4", 2, 131_000, 180_000)]


def rows(events):
    got = c5_score.interventions(Run(events, LOOPS, GOLD), core.c5(events, {"comments": {}}))
    assert len(got["rows"]) == got["total"]
    return got


def test_a_seller_price_after_a_proposal_takes_the_time_since_the_last_one_that_arrived_before_it():
    events = SP01 + [
        *said("p-0", 40_000, 42_000, "card-1", 250_000, 230_000, 44_000),
        *said("p-1", 47_100, 50_380, "card-1", 250_000, 220_000, 51_000),
        card("card-1", 3, 54_000, 220_000, "seller", arrived=54_500),
        *said("p-late", 55_000, 56_000, "card-1", 220_000, 210_000, 58_000),  # arrives after the seller's version
    ]  # fmt: skip
    got = rows(events)
    assert got["rows"] == [
        {"event_id": "card-1-v3", "loop": 1, "kind": "card", "took_ms": 3_500, "cause": ["price"]}
    ]
    assert (got["total"], got["by_cause"]) == (1, {"price_typed": 1})


@pytest.mark.parametrize(
    "change, cause",
    [
        ({"sku": "SP01-RED"}, ["price", "item"]),
        ({"name": "other item"}, ["price", "item"]),
        ({"stock": 0}, ["price", "stock"]),
        ({"variants": [{"label": "M", "stock": 1}]}, ["price", "stock"]),
        ({"sku": "SP01-RED", "stock": 0}, ["price", "item", "stock"]),
    ],
)
def test_a_seller_version_names_every_field_group_it_changes(change, cause):
    events = SP01 + [
        *said("p-1", 47_100, 50_380, "card-1", 250_000, 220_000, 51_000),
        card("card-1", 3, 54_000, 220_000, "seller", arrived=54_500, **change),
    ]
    assert [(r["took_ms"], r["cause"]) for r in rows(events)["rows"]] == [(3_500, cause)]


def test_a_seller_order_after_needs_seller_for_stock_has_cause_stock():
    events = SP01 + [
        *said("p-1", 47_100, 50_380, "card-1", 250_000, 220_000, 51_000),
        order("o-1", 60_000, "DRAFT", "system"),
        order("o-1", 61_000, "NEEDS_SELLER", "system", "qty 3 > stock 2"),
        order("o-1", 70_000, "CONFIRM_SENT", "seller"),
    ]
    got = rows(events)
    assert got["rows"] == [{"event_id": "o-1-CONFIRM_SENT", "loop": 1, "kind": "order", "took_ms": 19_000,
                            "cause": ["stock"]}]  # fmt: skip
    assert got["by_cause"] == {"order:over_stock->CONFIRM_SENT": 1}


@pytest.mark.parametrize(
    "reason, cause",
    [
        ("card has no price yet", ["price"]),
        ("unit price 250000 != live card price 220000", ["price"]),
        ("customer asked for a discount", ["price"]),
        ("amount 900000 > seller threshold 500000", ["price"]),
        ("variant M not on card v2", ["stock"]),
        ("card retired", ["item"]),
        ("something new", ["error"]),
        (None, ["error"]),
    ],
)
def test_an_order_cause_maps_from_the_needs_seller_reason(reason, cause):
    events = SP01 + [
        order("o-1", 61_000, "NEEDS_SELLER", "system", reason),
        order("o-1", 70_000, "CANCELLED", "seller"),
    ]
    assert [r["cause"] for r in rows(events)["rows"]] == [cause]


def test_a_seller_order_with_no_needs_seller_before_it_is_an_error():
    events = SP01 + [order("o-1", 70_000, "CANCELLED", "seller")]
    assert [r["cause"] for r in rows(events)["rows"]] == [["error"]]


def test_no_proposal_before_the_intervention_leaves_took_ms_none():
    events = SP01 + SP04 + [
        *said("p-4", 152_860, 155_260, "card-4", 180_000, 160_000, 157_060),  # another card
        card("card-1", 3, 54_000, 220_000, "seller", arrived=54_500),
        order("o-1", 61_000, "NEEDS_SELLER", "system", "qty 3 > stock 2"),
        order("o-1", 70_000, "CONFIRM_SENT", "seller"),
        *said("p-1", 80_000, 82_000, "card-1", 220_000, 210_000, 84_000),  # arrives after both
    ]  # fmt: skip
    assert [(r["event_id"], r["took_ms"]) for r in rows(events)["rows"]] == [
        ("card-1-v3", None),
        ("o-1-CONFIRM_SENT", None),
    ]


def test_took_ms_compares_arrival_times_not_source_times():
    """p-1 is spoken before both versions (ts_ms) but arrives with v3 and after v4: 0 for v3, None for v4."""
    events = SP01 + [
        *said("p-1", 47_100, 50_380, "card-1", 250_000, 220_000, 54_400),
        card("card-1", 3, 54_000, 220_000, "seller", arrived=54_400),
        card("card-1", 4, 54_100, 200_000, "seller", arrived=54_300),
    ]
    assert [(r["event_id"], r["took_ms"]) for r in rows(events)["rows"]] == [
        ("card-1-v3", 0),
        ("card-1-v4", None),
    ]


MIXED = SP01 + SP04 + [
    *said("p-both", 152_860, 155_260, "card-4", 180_000, 160_000, 157_060),  # SP04's card, SP04's fixed price
    *said("p-price", 47_100, 50_380, "card-1", 250_000, 220_100, 51_300),  # the held card, no gold price
    *said("p-card", 43_500, 46_300, "card-4", 180_000, 250_000, 47_700),  # SP01's price on SP04's card
]  # fmt: skip


def test_right_price_and_both_are_counted_beside_right_card():
    out = c5_score.proposals(Run(MIXED, LOOPS, GOLD))
    assert [(r["event_id"], r["right_card"], r["right_price"]) for r in out["rows"]] == [
        ("p-both", True, True),
        ("p-price", True, False),
        ("p-card", False, True),
    ]
    assert (out["n"], out["right_card"], out["right_price"], out["both"]) == (3, 2, 2, 1)


def test_the_report_prints_the_three_columns_and_the_intervention_rows():
    events = MIXED + [card("card-1", 3, 54_000, 220_000, "seller", arrived=54_500, sku="SP01-RED")]
    scored = c5_report.score(events, {"loops": LOOPS}, [], [], GOLD, [])
    assert [(lp["loop"], lp["right_card"], lp["right_price"], lp["both"]) for lp in scored["per_loop"]] == [
        (1, 2, 2, 1)
    ]
    assert c5_report.text(scored) == [
        "loop | proposals | right_card | right_price | both",
        "1 | 3 | 2 | 2 | 1",
        "total | 3 | 2 | 2 | 1",
        "interventions 1",
        "event_id | loop | took_ms | cause",
        "card-1-v3 | 1 | 3200 | price,item",
    ]
