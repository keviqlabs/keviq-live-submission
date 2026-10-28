"""C5 gold plus synthetic written-number variants and non-phone controls; no DB or model calls."""

import json
import unicodedata

import pytest

from app.settings import ROOT
from worker.brain.shield import scan

ROWS = [
    json.loads(line)
    for k in range(1, 6)
    for line in (ROOT / f"data/comments/c5/stream-{k}.jsonl").read_text().splitlines()
]
WORDS = "không một hai ba bốn năm sáu bảy tám chín".split()
WRITTEN_IDS = {"c5-1-012", "c5-2-011", "c5-3-012", "c5-4-013", "c5-5-013"}
WRITTEN = [row for row in ROWS if row["comment_id"] in WRITTEN_IDS]


def variants():
    out = []
    for row in WRITTEN:
        loop = int(row["comment_id"].split("-")[1])
        digits = f"09000000{loop}3"
        words = [WORDS[int(n)] for n in digits]
        mixed = [WORDS[int(n)] if i % 2 else n for i, n in enumerate(digits)]
        cases = {
            "dots": ". ".join(words),
            "mixed_chunk": digits[:4] + " " + " ".join(words[4:]),
            "mixed_dots": " . ".join(mixed),
            "uppercase": " ".join(words).upper(),
            "nfd": unicodedata.normalize("NFD", " ".join(words)),
            "international": "+84 " + " ".join(words[1:]),
        }
        out += [(f"{row['comment_id']}-{kind}", "gọi " + text) for kind, text in cases.items()]
    return out


VARIANTS = variants()
NEGATIVE = [
    ("price-words", "giá hai trăm năm mươi nghìn"),
    ("price-short", "hai trăm năm mươi"),
    ("price-digits", "giá 250.000 đồng"),
    ("size-words", "size ba tám"),
    ("size-digits", "size 38"),
    ("quantity", "lấy hai đôi"),
    ("quantity-color", "hai đôi màu đen"),
    ("color-short", "mã màu không ba tám"),
    ("color-code", "mã màu C0900000013X"),
    ("sku-code", "mã SP0900000013"),
    ("rgb", "màu RGB 255.000.100"),
    ("short-words", "không chín không không không không không không một"),
    ("short-mixed", "0900 không không một ba"),
    ("wrong-prefix", "một chín không không không không không không một ba"),
    ("overflow-words", "không không chín không không không không không không một ba"),
    ("overflow-digits", "00900000013"),
    ("overflow-spaces", "0 0 9 0 0 0 0 0 0 1 3"),
    ("overflow-dots", "0.0.9.0.0.0.0.0.0.1.3"),
    ("interrupted", "không chín không không sản phẩm không không không không một ba"),
    ("magnitude-break", "không chín không không trăm không không không không một ba"),
    ("size-break", "0 9 0 0 size 0 0 0 0 1 3"),
    ("long-run", " ".join(["không"] * 5000)),
]


def test_c5_fixture_counts_and_written_sources_are_fixed():
    assert len(ROWS) == 500 and sum(r["has_phone"] for r in ROWS) == 20
    assert {r["comment_id"] for r in WRITTEN} == {"c5-1-012", "c5-2-011", "c5-3-012", "c5-4-013", "c5-5-013"}
    assert len(VARIANTS) == 30


@pytest.mark.parametrize("row", ROWS, ids=[row["comment_id"] for row in ROWS])
def test_shield_matches_c5_phone_gold(row):
    assert ("phone_number" in scan(row["text"])) == row["has_phone"], row["comment_id"]


@pytest.mark.parametrize("case,text", VARIANTS, ids=[r[0] for r in VARIANTS])
def test_shield_detects_written_and_mixed_digit_phones(case, text):
    assert "phone_number" in scan(text), case


@pytest.mark.parametrize("case,text", NEGATIVE, ids=[r[0] for r in NEGATIVE])
def test_shield_does_not_hide_prices_sizes_quantities_or_identifiers(case, text):
    assert "phone_number" not in scan(text), case
