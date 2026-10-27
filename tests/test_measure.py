"""measure/: the C1, C3 and C5 numbers move when the events are wrong (a metric that cannot fail measures nothing)."""

import copy
import math

from measure.core import c1, c3, c5, report


def test_demo_fixtures_pass(demo_events, gold):
    r = report(demo_events, gold)
    assert r["c1"]["missing"] == [] and r["c3"]["accuracy"] == 1.0 and r["c5"]["order_recall"] == 1.0


def test_c1_reports_a_trigger_without_consequence(demo_events):
    events = [e for e in demo_events if "ps-b" not in (e.get("caused_by") or [])]
    assert "ps-b" in c1(events)["missing"]


def test_c1_consequence_outside_the_cycle_does_not_count(demo_events):
    events = copy.deepcopy(demo_events)
    for e in events:
        if "ps-a" in (e.get("caused_by") or []):
            e["ts_ms"] += 6000
    assert "ps-a" in c1(events)["missing"]


def test_c3_reports_a_wrong_reference(demo_events, gold):
    events = copy.deepcopy(demo_events)
    lab = next(e for e in events if e["type"] == "comment_labeled" and e["comment_id"] == "cm-08")
    lab["order_draft"]["referenced_card_id"] = "card-d"
    r = c3(events, gold)
    assert r["accuracy"] < 1 and r["wrong"] == [{"comment_id": "cm-08", "want": "card-b", "got": "card-d"}]


def test_c5_phone_never_hidden_is_infinite(demo_events, gold):
    events = copy.deepcopy(demo_events)
    next(e for e in events if e["event_id"] == "fx-lb-04")["action"] = "none"
    assert math.isinf(c5(events, gold)["phone_visible_max_s"])


def test_c5_phone_visible_ends_at_the_first_hide(demo_events, gold):
    # cm-04 at 12000 ms is hidden at 13500 ms; a second hide label for it arriving at 20000 ms does not make the
    # phone visible for 8 s
    events = copy.deepcopy(demo_events)
    first = next(e for e in events if e["event_id"] == "fx-lb-04")
    events.append(first | {"event_id": "fx-lb-04-model", "_replay_at_ms": 20000})
    assert c5(events, gold)["phone_visible_max_s"] == 1.5


def test_c5_receipt_with_a_different_price_is_not_ok(demo_events, gold):
    events = copy.deepcopy(demo_events)
    next(e for e in events if e["event_id"] == "fx-o11-3")["receipt"]["price"] = 900000
    r = c5(events, gold)
    assert (r["receipts"], r["receipts_ok"]) == (2, 1)


def test_c5_seller_edits_count_as_interventions(demo_events, gold):
    events = copy.deepcopy(demo_events)
    next(e for e in events if e["type"] == "card")["provenance"]["edited_by"] = "seller"
    assert c5(events, gold)["interventions"] == 1
