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
