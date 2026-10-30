"""The proposal rules ruled on at 04:18, on synthetic events and the committed C5 gold (windows.json):

- stale on arrival: a proposal whose closing version was ingested before it keeps its raw handling time, is
  counted apart, and stays out of the handling median; the median over every timed proposal is kept beside it;
- a wrong proposal is counted under card when its card is not the held item, else under price.
"""

import json

from app.settings import ROOT
from measure import c5_score
from measure.c5_score import Run
from measure.c5_shop import Gold

T0 = 1_000_000
LOOPS = [{"k": 1, "t0_ms": T0, "end_ms": T0 + 351_500}]
WINDOWS = json.loads((ROOT / "data" / "comments" / "c5" / "windows.json").read_text())
PRICES = {"SP01": {"price_first": 250_000, "price_fixed": 220_000},
          "SP04": {"price_first": 180_000, "price_fixed": 160_000}}  # fmt: skip
GOLD = Gold(WINDOWS, PRICES)


def card(cid, version, status, ts, price, frame=None, caused_by=()):
    provenance = {"edited_by": None} | ({} if frame is None else {"frame_ts_ms": T0 + frame})
    return {"type": "card", "event_id": f"{cid}-v{version}", "card_id": cid, "version": version, "status": status,
            "ts_ms": T0 + ts, "price": price, "sku": None, "caused_by": list(caused_by),
            "provenance": provenance}  # fmt: skip


def said(eid, start, end, card_id, current, heard, arrived):
    """The speech line and the proposal made from it; the proposal's ts_ms is the speech's (D-024)."""
    return [
        {"type": "speech", "event_id": f"s-{eid}", "ts_ms": T0 + start, "start_ms": T0 + start, "end_ms": T0 + end},
        {"type": "price_proposal", "event_id": eid, "card_id": card_id, "card_version": 2, "current_price": current,
         "heard_price": heard, "speech_event_id": f"s-{eid}", "ts_ms": T0 + start, "ingested_ms": T0 + arrived},
    ]  # fmt: skip


SP01 = [card("card-1", 1, "draft", 16_000, None, frame=15_000), card("card-1", 2, "live", 26_000, 250_000)]
SP04 = [card("card-4", 1, "draft", 126_500, None, frame=125_000), card("card-4", 2, "live", 131_000, 180_000)]


def test_a_proposal_closed_before_it_arrived_is_stale_and_kept_out_of_the_median():
    events = SP01 + SP04 + [
        *said("p-1", 43_500, 46_300, "card-1", 250_000, 230_000, 47_700),
        *said("p-2", 47_100, 50_380, "card-1", 250_000, 220_100, 51_049),
        card("card-1", 3, "edited", 51_000, 220_000),  # the seller types a price, 49 ms before p-2 arrives
        *said("p-3", 152_860, 155_260, "card-4", 180_000, 160_000, 157_060),
        card("card-4", 3, "edited", 161_000, 160_000, caused_by=["p-3"]),
    ]  # fmt: skip
    out = c5_score.proposals(Run(events, LOOPS, GOLD))
    assert out["handling_ms"] == {"median": 3_620, "n": 2}
    assert out["handling_ms_all"] == {"median": 3_300, "n": 3}
    rows = {r["event_id"]: r for r in out["rows"]}
    assert [(e, rows[e]["outcome"], rows[e]["stale"], rows[e]["handling_ms"]) for e in rows] == [
        ("p-1", "fixed", False, 3_300),
        ("p-2", "fixed", True, -49),
        ("p-3", "used", False, 3_940),
    ]
    assert (out["stale"]["n"], out["stale"]["ids"]) == (1, ["p-2"])


def test_a_wrong_card_and_a_wrong_price_are_counted_apart():
    events = SP01 + SP04 + [
        *said("p-price", 47_100, 50_380, "card-1", 250_000, 220_100, 51_300),  # the held card, no gold price
        *said("p-card", 43_500, 46_300, "card-4", 180_000, 250_000, 47_700),  # SP01's price on SP04's card
    ]  # fmt: skip
    out = c5_score.proposals(Run(events, LOOPS, GOLD))
    rows = {r["event_id"]: r for r in out["rows"]}
    assert [(e, rows[e]["right_card"], rows[e]["right_price"], rows[e]["wrong"]) for e in rows] == [
        ("p-price", True, False, True),
        ("p-card", False, True, True),
    ]
    assert (out["wrong"]["n"], out["wrong"]["card"], out["wrong"]["price"]) == (2, 1, 1)
