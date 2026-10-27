"""worker/voice/vn.py on the test1 transcript as heard (data/local/test1/probe/voice.json, local, not committed).

Expected readings are row numbers and values only. Rows 8, 9, 31 and 55 are the misread prices fixed in vn.py (a
repeated "mươi"; a "một" counting the item after "nghìn"); every other row reads as it did before the fix.
"""

import json
from pathlib import Path

import pytest

from worker.brain import card
from worker.voice import vn

VOICE = Path(__file__).resolve().parent.parent / "data/local/test1/probe/voice.json"

# row -> price; before the fix these read 230 000, 220 100, 160 100 and 600 100
FIXED = {8: 220_000, 9: 220_000, 31: 160_000, 55: 600_000}
# the 17 other rows with a price, the sizes and the colours: as read before the fix
KEPT = {
    3: 250_000,
    11: 320_000,
    15: 320_000,
    18: 280_000,
    26: 180_000,
    30: 160_000,
    34: 150_000,
    36: 150_000,
    38: 210_000,
    39: 210_000,
    42: 450_000,
    44: 450_000,
    46: 550_000,
    49: 650_000,
    54: 600_000,
    58: 850_000,
    62: 250_000,
}
SIZES = {
    4: ["40", "2"],
    5: ["41", "1"],
    11: ["39"],
    13: ["40", "2"],
    19: ["41"],
    21: ["42", "1"],
    26: ["M"],
    28: ["L", "33"],
    35: ["M", "1", "L", "2"],
    38: ["L", "2", "XL", "1"],
    62: ["40", "41"],
}
COLOURS = {
    2: ["đen"],
    7: ["đen"],
    10: ["trắng"],
    14: ["trắng"],
    15: ["trắng"],
    17: ["xám"],
    18: ["xám"],
    24: ["đen"],
    25: ["đen"],
    26: ["đen"],
    30: ["đen"],
    33: ["trắng"],
    34: ["trắng"],
    37: ["xanh"],
    38: ["xanh"],
    39: ["xanh"],
    41: ["đen"],
    42: ["đen"],
    44: ["đen"],
    45: ["trắng"],
    46: ["trắng"],
    47: ["trắng"],
    49: ["đen"],
    54: ["đen"],
    57: ["trắng"],
    58: ["trắng"],
    61: ["đen"],
}


@pytest.fixture(scope="module")
def heard() -> list[vn.Spoken]:
    if not VOICE.exists():
        pytest.skip("local test1 transcript not present (data/local/test1, not committed)")
    return [vn.parse(r["text"], set(card.COLORS)) for r in json.loads(VOICE.read_text())["rows"]]


def test_the_misread_prices_read_as_said(heard):
    assert {i: heard[i].price for i in FIXED} == FIXED


def test_the_other_prices_read_as_before(heard):
    assert len(KEPT) == 17
    assert {i: s.price for i, s in enumerate(heard) if s.price is not None and i not in FIXED} == KEPT


def test_rows_without_a_price_still_have_none(heard):
    none = [i for i, s in enumerate(heard) if s.price is None]
    assert len(heard) == 64 and len(none) == 43
    assert none == [i for i in range(64) if i not in KEPT | FIXED]


def test_sizes_and_colours_read_as_before(heard):
    assert {i: s.sizes for i, s in enumerate(heard) if s.sizes} == SIZES
    assert {i: s.colors for i, s in enumerate(heard) if s.colors} == COLOURS
