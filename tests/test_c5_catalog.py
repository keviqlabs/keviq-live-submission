"""data/comments/c5/catalog.json: the C5 shop catalog, checked against the test1 gold and the C5 order gold.

The check set for C5 (28/10), in three parts:
(a) every product code, size and stock in the generated catalog matches the local test1 gold;
(b) an order whose expected_action is a normal draft and confirmation (confirm_order) fits the catalog: its size
    exists and the stock covers the quantity, also summed over one loop;
(c) the orders written to go past the stock or to leave out the size stay in the gold as they are: they do not fit
    the catalog, and their expected_action is to ask the customer or to go to the seller. They count in the shop's
    interventions; a report may break the total down by cause, never subtract them.
(a) needs the local gold (data/local/test1, not committed); (b) and (c) read committed files only.
"""

import importlib.util
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from app.settings import ROOT

C5 = ROOT / "data" / "comments" / "c5"
CATALOG = {i["sku"]: i for i in json.loads((C5 / "catalog.json").read_text())}
STREAMS = {
    k: [json.loads(line) for line in (C5 / f"stream-{k}.jsonl").read_text().splitlines()] for k in range(1, 6)
}
ORDERS = [r for rows in STREAMS.values() for r in rows if r["label"] == "order" and r["order"]["product"]]
GOLD_PATH = Path(os.environ.get("C5_GOLD") or ROOT / "data" / "local" / "test1" / "labels.json")


def variant(order):
    """The catalog variant the order names (size and, when written, colour), else None."""
    return next(
        (
            v
            for v in CATALOG[order["product"]]["variants"]
            if v["size"] == order["size"] and order["color"] in (None, v["color"])
        ),
        None,
    )


def fits(order):
    v = variant(order)
    return v is not None and v["stock"] >= order["qty"]


def test_the_catalog_holds_the_ten_codes_in_the_shape_of_data_catalog():
    base = set(json.loads((ROOT / "data" / "catalog.json").read_text())[0])
    assert list(CATALOG) == [f"SP{n:02d}" for n in range(1, 11)]
    for sku, item in CATALOG.items():
        assert base <= set(item) and item["category"] in item["categories"], sku
        assert item["name"].split()[0] == sku, sku
        assert item["variants"] and all(v["stock"] >= 1 for v in item["variants"]), sku


def test_normal_orders_fit_the_catalog():
    normal = [r for r in ORDERS if r["expected_action"] == "confirm_order"]
    assert len(normal) >= 50
    assert [r["comment_id"] for r in normal if not fits(r["order"])] == []
    for k, rows in STREAMS.items():
        held = Counter()
        for r in (r for r in rows if r in normal):
            held[r["order"]["product"], r["order"]["size"]] += r["order"]["qty"]
        for (sku, size), n in held.items():
            assert n <= variant({"product": sku, "size": size, "color": None})["stock"], (k, sku, size, n)


def test_hard_orders_stay_in_the_gold_and_do_not_fit_the_catalog():
    hard = [r for r in ORDERS if not fits(r["order"])]
    by_action = Counter(r["expected_action"] for r in hard)
    assert by_action == {"ask_customer": 20, "needs_seller": 5}, by_action
    for r in hard:
        o = r["order"]
        sizes = {v["size"] for v in CATALOG[o["product"]]["variants"]}
        if r["expected_action"] == "ask_customer":  # no size written for an item that comes in several
            assert o["size"] is None and len(sizes) > 1, r
        else:  # more than the stock: the seller decides
            assert variant(o) and o["qty"] > variant(o)["stock"], r
    sizes = {sku: {v["size"] for v in item["variants"]} for sku, item in CATALOG.items()}
    wrong = [
        r for r in ORDERS if r["order"]["size"] and r["order"]["size"] not in sizes[r["order"]["product"]]
    ]
    assert wrong == []  # no order names a size the catalog does not have


def author():
    spec = importlib.util.spec_from_file_location("author_c5", C5 / "author_c5.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(
    not GOLD_PATH.exists(), reason="local test1 gold not present (data/local/test1, not committed)"
)
def test_the_catalog_matches_the_local_test1_gold():
    gold = json.loads(GOLD_PATH.read_text())
    assert list(CATALOG) == sorted(gold["products"])
    for sku, p in gold["products"].items():
        item = CATALOG[sku]
        assert item["kind"] == p["kind"] and item["price"] == p["price_first"], sku
        assert item["categories"] == gold["category_map"][p["kind"]], sku
        assert {v["size"] or "one": v["stock"] for v in item["variants"]} == p["stock"], sku
        assert {v["color"] for v in item["variants"]} == {p["color"]}, sku
        heard = p["name"].lower() in item["name"].lower()
        assert not heard, sku  # no name heard in the video
    assert author().catalog(gold) == list(CATALOG.values())
