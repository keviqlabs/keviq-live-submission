"""measure.c5.retire_events: the runner closes the open cards at a loop boundary, and that close is not a seller edit."""

from app.contracts import make_event
from measure import c5
from measure.c5_shop import Feed


def version(v, status, edited_by, price=220000):
    return make_event("card", "s", 1000 + v, "replay", event_id=f"card-a-v{v}", card_id="card-a", version=v,
                      sku="SP01", name="Áo", price=price, currency="VND", variants=[], stock=3, image_url=None,
                      status=status, provenance={"frame_ts_ms": 500, "speech_event_ids": ["u1"],
                                                 "edited_by": edited_by})  # fmt: skip


def feed(*versions):
    out = Feed()
    for seq, ev in enumerate(versions, 1):
        out.add(seq, ev)
    return out


def test_a_retire_after_a_seller_price_is_not_a_seller_edit():
    (retired,) = c5.retire_events(feed(version(1, "live", None), version(2, "edited", "seller", 250000)), "s")
    assert (retired["version"], retired["status"], retired["price"]) == (3, "retired", 250000)
    assert retired["provenance"] == {"frame_ts_ms": 500, "speech_event_ids": ["u1"], "edited_by": None}


def test_a_retired_card_is_not_retired_again():
    assert c5.retire_events(feed(version(1, "live", None), version(2, "retired", None)), "s") == []
