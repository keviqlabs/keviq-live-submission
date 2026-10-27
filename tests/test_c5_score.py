"""measure/c5_score.py on synthetic events and the committed C5 gold (data/comments/c5/windows.json).

- a card stands for the gold item whose span holds the frame that opened it, not the item its live span overlaps
  most: a card that took a price stays live to the loop end;
- references keep core.c3's definition: the latest label's referenced_card_id, n is every gold order comment that
  names an item and a current or previous card, and a comment with no card is wrong, never out of n;
- order_ids names every posted gold order comment, with what the system made of it and why a wrong one is wrong;
- waits count one per (loop, reason, line event id) and sum the loops (HQ419).
"""

import json

from app.settings import ROOT
from measure import c5_score
from measure.c5_score import Run
from measure.c5_shop import Gold, cause
from orders.flow import order_id_for

T0 = 1_000_000
LOOPS = [{"k": 1, "t0_ms": T0, "end_ms": T0 + 351_500}]
GOLD = Gold(json.loads((ROOT / "data" / "comments" / "c5" / "windows.json").read_text()), {})


def card(cid, version, status, ts, frame=None, price=None):
    return {"type": "card", "event_id": f"{cid}-v{version}", "card_id": cid, "version": version, "status": status,
            "ts_ms": T0 + ts, "price": price, "sku": None, "caused_by": [],
            "provenance": {} if frame is None else {"frame_ts_ms": T0 + frame}}  # fmt: skip


def opened(cid, frame, live_ms, retired_ms=None):
    """A card drafted from the frame at `frame`, live with a price at `live_ms`, retired at `retired_ms` or never."""
    out = [card(cid, 1, "draft", frame + 1500, frame), card(cid, 2, "live", live_ms, price=250_000)]
    return out + ([card(cid, 3, "retired", retired_ms)] if retired_ms else [])


def comment(cid, ts):
    return {"type": "comment", "event_id": f"e-{cid}", "comment_id": cid, "ts_ms": T0 + ts}


def labeled(cid, ts, card_id, kind="current_card"):
    draft = {"sku": None, "variant": None, "qty": 1, "referenced_card_id": card_id, "reference_kind": kind}
    return {"type": "comment_labeled", "event_id": f"l-{cid}", "comment_id": cid, "ts_ms": T0 + ts, "label": "order",
            "action": "none", "order_draft": draft}  # fmt: skip


def order(cid, ts, card_id):
    return {"type": "order", "event_id": f"o-{cid}", "order_id": order_id_for(cid), "ts_ms": T0 + ts,
            "state": "DRAFT", "prev_state": None, "actor": "system", "reason": None,
            "lines": [{"card_id": card_id, "card_version": 2, "sku": None}]}  # fmt: skip


def ask(cid, ts, card_id):
    return {"type": "message_out", "event_id": f"m-{cid}", "template": "ask_variant", "ts_ms": T0 + ts,
            "payload": {"order_id": order_id_for(cid), "card_id": card_id}}  # fmt: skip


def row(cid, product, kind="current_card"):
    return {"comment_id": cid, "label": "order", "expected_action": "confirm_order",
            "order": {"product": product, "reference_kind": kind}}  # fmt: skip


def test_a_card_stands_for_the_item_of_its_opening_frame():
    # opened on SP01 (span 10000..49400) and live to the loop end: its live span overlaps SP04 the most
    run = Run(opened("card-a", 15_000, 26_000), LOOPS, GOLD)
    assert run.item["card-a"] == "SP01"


def test_a_returning_item_and_the_item_before_it_keep_their_own_cards():
    events = opened("card-b", 300_000, 323_000) + opened("card-c", 330_000, 334_000)
    run = Run(events, LOOPS, GOLD)
    assert (run.item["card-b"], run.item["card-c"]) == ("SP10", "SP01")


def test_a_card_outside_the_loops_has_no_item():
    run = Run([card("card-x", 1, "draft", -5_000, -5_000)], LOOPS, GOLD)
    assert run.item["card-x"] is None


def test_references_count_a_comment_with_no_card_as_wrong():
    events = opened("card-a", 15_000, 26_000, 49_000) + [
        comment("c-1", 30_000), labeled("c-1", 31_000, "card-a"), order("c-1", 31_500, "card-a"),
        comment("c-2", 40_000), labeled("c-2", 41_000, None),
        comment("c-3", 45_000), labeled("c-3", 46_000, None, "unresolved"),
    ]  # fmt: skip
    rows = [row("c-1", "SP01"), row("c-2", "SP01"), row("c-3", None, "unresolved")]
    out = c5_score.references(Run(events, LOOPS, GOLD), rows)
    assert (out["n"], out["right"], out["no_card"], out["accuracy"]) == (2, 1, 1, 0.5)
    assert [w["comment_id"] for w in out["wrong"]] == ["c-2"]


def test_references_score_the_card_the_label_drafted():
    events = opened("card-a", 15_000, 26_000, 49_000) + opened("card-d", 60_000, 76_000, 86_000) + [
        comment("c-1", 80_000), labeled("c-1", 81_000, "card-d"), order("c-1", 81_500, "card-a"),
    ]  # fmt: skip
    out = c5_score.references(Run(events, LOOPS, GOLD), [row("c-1", "SP02")])
    assert (out["n"], out["right"]) == (1, 1)
    assert out["disagree"] == [
        {"comment_id": "c-1", "label_card": "card-d", "card": "card-a", "source": "order"}
    ]


def test_references_reach_the_gate_only_on_enough_comments():
    events = opened("card-a", 15_000, 26_000, 49_000)
    rows = []
    for j in range(50):
        cid = f"c-{j}"
        events += [comment(cid, 27_000 + j), labeled(cid, 27_500 + j, "card-a")]
        rows.append(row(cid, "SP01"))
    assert c5_score.references(Run(events, LOOPS, GOLD), rows[:49])["gate"] is False
    assert c5_score.references(Run(events, LOOPS, GOLD), rows)["gate"] is True


def test_order_ids_name_every_gold_order_comment():
    events = opened("card-a", 15_000, 26_000) + opened("card-d", 60_000, 76_600) + [
        comment("c-ok", 30_000), labeled("c-ok", 31_000, "card-a"), order("c-ok", 31_500, "card-a"),
        comment("c-early", 71_100), labeled("c-early", 72_000, "card-a"), order("c-early", 72_500, "card-a"),
        comment("c-ask", 103_000), labeled("c-ask", 104_000, "card-d"), ask("c-ask", 104_500, "card-d"),
        comment("c-back", 107_000), labeled("c-back", 108_000, "card-a", "previous_card"),
        order("c-back", 108_500, "card-a"),
        comment("c-none", 3_000), labeled("c-none", 4_000, None, "unresolved"),
        comment("c-quiet", 50_000),
    ]  # fmt: skip
    rows = [row("c-ok", "SP01"), row("c-early", "SP02"), row("c-ask", "SP03"), row("c-back", "SP02", "previous_card"),
            row("c-none", None, "unresolved"), row("c-quiet", "SP01"), row("c-unposted", "SP01")]  # fmt: skip
    out = c5_score.order_ids(Run(events, LOOPS, GOLD), rows)
    by = {r["comment_id"]: r for r in out["rows"]}
    assert set(by) == {"c-ok", "c-early", "c-ask", "c-back", "c-none", "c-quiet"}
    assert {c: r["outcome"] for c, r in by.items()} == {
        "c-ok": "order", "c-early": "order", "c-ask": "ask_variant", "c-back": "order", "c-none": "unresolved",
        "c-quiet": "other",
    }  # fmt: skip
    assert {c: r["why"] for c, r in by.items()} == {
        "c-ok": None, "c-early": "item_card_live_after_comment", "c-ask": "no_card_for_item",
        "c-back": "item_card_live_before_comment", "c-none": None, "c-quiet": "no_card",
    }  # fmt: skip
    assert (out["n"], out["in_n"], out["right"]) == (6, 5, 1)
    assert by["c-ask"]["card"] == "card-d" and by["c-ask"]["card_source"] == "ask_variant"


def test_order_ids_and_references_share_one_n():
    events = opened("card-a", 15_000, 26_000, 49_000) + [
        comment("c-1", 30_000), labeled("c-1", 31_000, "card-a"),
        comment("c-2", 40_000), labeled("c-2", 41_000, None),
        comment("c-3", 3_000), labeled("c-3", 4_000, None, "unresolved"),
    ]  # fmt: skip
    rows = [row("c-1", "SP01"), row("c-2", "SP01"), row("c-3", None, "unresolved")]
    run = Run(events, LOOPS, GOLD)
    ids, refs = c5_score.order_ids(run, rows), c5_score.references(run, rows)
    assert (ids["in_n"], ids["right"]) == (refs["n"], refs["right"])


def proposal(eid, ts, arrived, card_id, card_version, current, heard):
    """A price_proposal: ts_ms is the time of its speech (D-024), it arrives after the hold."""
    return {"type": "price_proposal", "event_id": eid, "card_id": card_id, "card_version": card_version,
            "current_price": current, "heard_price": heard, "speech_event_id": f"s-{eid}", "ts_ms": T0 + ts,
            "ingested_ms": T0 + arrived}  # fmt: skip


def edited(cid, version, ts, price, caused_by=()):
    """A card version that keeps edited_by "seller": the route writes it, and worker versions copy it."""
    return {**card(cid, version, "edited", ts, price=price), "caused_by": list(caused_by),
            "provenance": {"edited_by": "seller"}, "ingested_ms": T0 + ts}  # fmt: skip


def test_a_proposal_closes_on_the_first_newer_version_with_another_price():
    # D-024: a worker version copies edited_by and keeps the price, so it closes nothing, also when it arrives
    # between the speech and the proposal; the seller's version that applies the proposal does
    events = opened("card-a", 15_000, 26_000) + [
        edited("card-a", 3, 40_000, 220_000), proposal("p-1", 44_000, 60_000, "card-a", 3, 220_000, 250_000),
        edited("card-a", 4, 45_000, 220_000), edited("card-a", 5, 64_000, 250_000, ["p-1"]),
    ]  # fmt: skip
    out = c5_score.proposals(Run(events, LOOPS, GOLD))
    assert [(r["outcome"], r["closed_by"], r["handling_ms"]) for r in out["rows"]] == [
        ("used", "card-a-v5", 4000)
    ]


def test_a_proposal_on_an_older_version_is_not_closed_by_it():
    # the seller's typed price (v3) arrives before a proposal made on v3; a later proposal is applied (v4)
    events = opened("card-a", 15_000, 26_000) + [
        proposal("p-1", 30_000, 36_000, "card-a", 2, 250_000, 230_000), edited("card-a", 3, 40_000, 220_000),
        proposal("p-2", 39_000, 40_050, "card-a", 3, 220_000, 220_100),
        proposal("p-3", 90_000, 96_000, "card-a", 3, 220_000, 250_000), edited("card-a", 4, 100_000, 250_000, ["p-3"]),
    ]  # fmt: skip
    out = c5_score.proposals(Run(events, LOOPS, GOLD))
    assert [(r["event_id"], r["outcome"], r["handling_ms"]) for r in out["rows"]] == [
        ("p-1", "fixed", 4000),
        ("p-2", "replaced", None),
        ("p-3", "used", 4000),
    ]
    assert out["handling_ms"] == {"median": 4000, "n": 2}


def wait(eid, reason, sid, suffix):
    return f"price wait: {eid} reason {reason} candidates 2; session {sid} waits {suffix}"


def test_waits_dedupe_per_loop_and_sum_the_loops():
    loops = [
        {"k": 1, "waits": {"many": 2},
         "wait_lines": [wait("s1", "many", "x", "many=1"), wait("s1", "many", "x", "many=1"),
                        wait("s2", "many", "x", "many=2")]},
        {"k": 2, "waits": {"many": 1, "none": 1},
         "wait_lines": [wait("s1", "many", "x", "many=1"), wait("s3", "none", "x", "many=1 none=1")]},
    ]  # fmt: skip
    out = c5_score.waits(loops)
    assert out["total"] == {"many": 3, "none": 1}
    assert out["all_agree"] is True


def test_waits_flag_a_snapshot_that_disagrees():
    loops = [{"k": 1, "waits": {"many": 3}, "wait_lines": [wait("s1", "many", "x", "many=1")]}]
    assert c5_score.waits(loops)["per_loop"][1]["agree"] is False


def test_verdict_and_cause():
    assert c5_score.verdict([("not_triggered", None)])["status"] == "NOT_TRIGGERED"
    assert c5_score.verdict([("pass", "e1"), ("fail", "e2")])["event_id"] == "e2"
    assert c5_score.verdict([("pass", "e1")])["status"] == "PASS"
    assert cause("qty 3 > stock 2") == "over_stock"
    assert cause("something new") == "other"
