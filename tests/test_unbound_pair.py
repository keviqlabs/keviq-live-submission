"""The (current, previous) pair a session without an attachment binding orders from (labels.live_cards_at).

current is the most recently shown item by frame time whose version known at the comment is not retired, an
unpriced draft included; previous is the next most recently shown item whose version known at the comment is not
retired, or is retired but was live or edited (priced) in a version known at the comment. An item put away after
it had a price is still "the one before"; a draft put away before it ever had a price is skipped.
"""

from worker.brain.labels import cards_at, resolve


def version(card_id, number, status, price, ts_ms, frame_ts_ms):
    """A card version as the store keeps it: only the fields the timeline reads."""
    return {
        "card_id": card_id,
        "version": number,
        "status": status,
        "price": price,
        "sku": card_id.upper(),
        "ts_ms": ts_ms,
        "provenance": {"frame_ts_ms": frame_ts_ms},
    }


def pair(versions, ts_ms):
    current, previous = cards_at(versions, ts_ms, live_only=True)
    return tuple(card and (card["card_id"], card["version"]) for card in (current, previous))


def bound(kind, versions, ts_ms):
    item = {"reference_kind": kind, "sku": None, "size": None, "color": None, "qty": 1}
    return resolve(item, versions, ts_ms, set(), live_only=True)["referenced_card_id"]


# The card rows of a recorded judge1 run, in ms from its first frame: each item opens as an unpriced draft and is
# priced a few seconds later; the third item is never priced.
RECORDED = [
    version("giay-01", 1, "draft", None, 2_751, 0),
    version("giay-01", 2, "live", 450_000, 8_690, 0),
    version("giay-01", 3, "live", 450_000, 10_399, 0),  # the image is added, same price
    version("tui-03", 1, "draft", None, 22_388, 20_000),
    version("tui-03", 2, "live", 690_000, 28_190, 20_000),
    version("boot-02", 1, "draft", None, 41_703, 40_000),
]


def test_the_one_before_an_unpriced_draft_is_the_item_shown_before_it():
    assert pair(RECORDED, 15_000) == (("giay-01", 3), None)
    assert bound("current_card", RECORDED, 15_000) == "giay-01"
    # "the pair from just now" at 26 s, while the bag is an unpriced draft on screen
    assert pair(RECORDED, 26_000) == (("tui-03", 1), ("giay-01", 3))
    assert bound("previous_card", RECORDED, 26_000) == "giay-01"
    # "the red one from before" at 47 s, while the boot is an unpriced draft on screen
    assert pair(RECORDED, 47_000) == (("boot-02", 1), ("tui-03", 2))
    assert bound("previous_card", RECORDED, 47_000) == "tui-03"


# The demo shape: card-03 is put away at 199.5 s and card-04 appears at 200 s.
RETIRED = [
    version("card-02", 1, "live", 690_000, 70_000, 70_000),
    version("card-03", 1, "live", 890_000, 140_000, 140_000),
    version("card-03", 2, "retired", 890_000, 199_500, 140_000),
    version("card-04", 1, "live", 920_000, 200_000, 200_000),
]


def test_a_retired_item_is_never_current_but_is_still_the_one_before():
    # Right after the next item appears, the item just put away is "the one before".
    assert pair(RETIRED, 202_000) == (("card-04", 1), ("card-03", 2))
    assert bound("previous_card", RETIRED, 202_000) == "card-03"
    # Between the retirement and the next item, the retired card is not current.
    assert pair(RETIRED, 199_800) == (("card-02", 1), ("card-03", 2))
    # A card retired only after the comment is taken at its version known then.
    assert pair(RETIRED, 199_000) == (("card-03", 1), ("card-02", 1))


# The card rows of another recorded judge1 run, in ms from its first frame: a black-boot draft opens at 43.5 s and is
# put away 2.5 s later without ever getting a price, when the brown boot replaces it.
NEVER_PRICED = [
    version("giay-01", 1, "draft", None, 2_521, 0),
    version("giay-01", 2, "live", 450_000, 8_798, 0),
    version("tui-03", 1, "draft", None, 23_918, 22_000),
    version("tui-03", 2, "live", 690_000, 28_178, 22_000),
    version("boot-02", 1, "draft", None, 43_522, 40_000),
    version("boot-02", 2, "retired", None, 46_063, 40_000),
    version("boot-05", 1, "draft", None, 46_069, 43_000),
    version("boot-05", 2, "live", 920_000, 49_181, 43_000),
]


def test_a_retired_item_that_never_had_a_price_is_not_the_one_before():
    # "the pair from just now" at 26 s, while the bag is an unpriced draft on screen
    assert pair(NEVER_PRICED, 26_000) == (("tui-03", 1), ("giay-01", 2))
    assert bound("previous_card", NEVER_PRICED, 26_000) == "giay-01"
    # "the red one from before" at 47 s: the black boot was put away unpriced, so the bag is the one before
    assert pair(NEVER_PRICED, 47_000) == (("boot-05", 1), ("tui-03", 2))
    assert bound("previous_card", NEVER_PRICED, 47_000) == "tui-03"
    # "three red bags" at 62 s names the bag, which is the one before the brown boot
    assert pair(NEVER_PRICED, 62_000) == (("boot-05", 2), ("tui-03", 2))
    item = {"reference_kind": "explicit_sku", "sku": "TUI-03", "size": None, "color": None, "qty": 3}
    order = resolve(item, NEVER_PRICED, 62_000, {"GIAY-01", "TUI-03", "BOOT-02", "BOOT-05"}, live_only=True)
    assert (order["referenced_card_id"], order["sku"], order["reference_kind"], order["qty"]) == (
        "tui-03",
        "TUI-03",
        "explicit_sku",
        3,
    )
    # A retired item that was live before it was put away is still "the one before".
    assert bound("previous_card", RETIRED, 202_000) == "card-03"


def test_an_item_shown_again_is_current_and_the_one_between_is_previous():
    versions = [
        version("card-A", 1, "live", 150_000, 1_000, 1_000),
        version("card-B", 1, "live", 200_000, 5_000, 5_000),
        version("card-A", 2, "live", 150_000, 9_000, 9_000),  # shown again
    ]
    assert pair(versions, 6_000) == (("card-B", 1), ("card-A", 1))
    assert pair(versions, 9_500) == (("card-A", 2), ("card-B", 1))
    assert bound("previous_card", versions, 9_500) == "card-B"
