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


def test_one_after_a_thousands_unit_before_an_item_word_is_the_count():
    # "… nghìn một chiếc" is the price of one item, not the price plus 100
    assert vn.parse("ba trăm bốn mươi nghìn một chiếc", COLORS).price == 340_000
    assert vn.parse("giá 180 nghìn một bộ nha", COLORS).price == 180_000


def test_a_repeated_muoi_reads_as_one():
    # the transcriber can repeat "mươi"; "bốn mươi mươi" is still forty
    assert vn.parse("ba trăm bốn mươi mươi nghìn thôi", COLORS).price == 340_000
    assert vn.parse("năm mươi mươi nghìn", COLORS).price == 50_000


@pytest.mark.parametrize(
    ("text", "price", "sizes", "colors"),
    [
        ("giá một triệu một", 1_100_000, [], []),
        ("giá một triệu một nhé", 1_100_000, [], []),
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
