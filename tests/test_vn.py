"""worker/voice/vn.py: the rule reader that takes over when Spark cannot read the seller's price."""

import pytest

from worker.voice import vn

COLORS = {"trắng", "đen", "đỏ", "nâu", "kem"}


@pytest.mark.parametrize(
    ("text", "price", "sizes", "colors"),
    [
        ("đôi này 450 nghìn, còn size 38 39 40 màu trắng", 450_000, ["38", "39", "40"], ["trắng"]),
        ("giá 1tr2 nha các chị", 1_200_000, [], []),
        ("một triệu rưỡi thôi", 1_500_000, [], []),
        ("bốn trăm rưỡi một đôi", 450_000, [], []),
        ("450.000 đồng, size 38,39", 450_000, ["38", "39"], []),
        ("350k size S, M, L", 350_000, ["S", "M", "L"], []),
        ("năm trăm lẻ năm nghìn", 505_000, [], []),
        ("tám mươi lăm nghìn", 85_000, [], []),
        ("size ba mươi tám màu đen", None, ["38"], ["đen"]),
        ("chào cả nhà, hôm nay nhiều đồ đẹp", None, [], []),
    ],
)
def test_reads_price_sizes_and_colours(text, price, sizes, colors):
    spoken = vn.parse(text, COLORS)
    assert (spoken.price, spoken.sizes, spoken.colors) == (price, sizes, colors)


def test_counts_and_out_of_range_numbers_are_not_prices():
    assert vn.parse("mua 2 đôi").price is None
    assert vn.parse("ship 5 nghìn").price is None  # below 10 000
    assert vn.parse("hai trăm triệu").price is None  # above 100 000 000


@pytest.mark.parametrize(
    ("text", "price"),
    [
        ("250 nghìn một đôi", 250_000),
        ("250 nghìn hai chiếc", 250_000),
        ("ba trăm bốn mươi nghìn một chiếc", 340_000),
        ("giá 180 nghìn một bộ nha", 180_000),
    ],
)
def test_a_bare_digit_after_a_thousands_unit_before_an_item_word_is_the_count(text, price):
    # "… nghìn hai chiếc" is the price and how many items, not the price plus 200
    assert vn.parse(text, COLORS).price == price


@pytest.mark.parametrize(
    "text", ["hai trăm năm mươi mươi nghìn", "ba trăm bốn mươi mươi nghìn thôi", "năm mươi mươi nghìn"]
)
def test_a_run_with_a_repeated_muoi_gives_no_price(text):
    # "năm mươi mươi" is a transcription slip: the run gives no price rather than a guess
    assert vn.parse(text, COLORS).price is None


def test_only_the_run_with_the_repeated_muoi_loses_its_price():
    assert vn.price_value(["hai", "trăm", "năm", "mươi", "mươi", "nghìn"]) is None
    assert vn.parse("hai trăm năm mươi mươi nghìn, à 250 nghìn", COLORS).price == 250_000


@pytest.mark.parametrize(
    ("text", "price", "sizes", "colors"),
    [
        ("một triệu hai", 1_200_000, [], []),
        ("giá 1tr2 đôi", 1_200_000, [], []),  # a numeral after "tr" is its next place, not a count
        ("giá một triệu một", 1_100_000, [], []),
        ("giá một triệu một nhé", 1_100_000, [], []),
        ("ba trăm năm mươi một cái", 351_000, [], []),  # "một" after "mươi" is a digit
        ("ba trăm năm mươi nghìn size bốn mươi", 350_000, ["40"], []),
        ("size ba mươi tám ba mươi chín màu kem", None, ["38", "39"], ["kem"]),
    ],
)
def test_readings_the_two_fixes_leave_alone(text, price, sizes, colors):
    spoken = vn.parse(text, COLORS)
    assert (spoken.price, spoken.sizes, spoken.colors) == (price, sizes, colors)


def test_a_bare_digit_after_a_unit_is_still_its_next_place():
    assert vn.price_value(["năm", "nghìn", "hai"]) == 5_200
    assert vn.price_value(["một", "triệu", "một"]) == 1_100_000
