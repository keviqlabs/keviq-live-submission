"""data/comments/c5: test1 played 5 times, 100 synthetic comments per loop plus the customer's Messenger replies.

Checks the schema, the minimum count per situation, the phone pattern, ts bounds, unique ids and texts, and that
orders/intent reads the cancel and discount wording where the gold says. The local test1 gold (data/local/test1,
not committed) is used only when present.
"""

import importlib.util
import json
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path

import pytest

from app.settings import ROOT
from orders.intent import cancel_request, customer_reply, discount_request
from worker.brain.shield import LINK, PHONE

C5 = ROOT / "data" / "comments" / "c5"
LOOP_MS = 351500
LABELS = {"order", "scam", "stock_question", "price_question", "other", "complaint", "spam"}
KEYS = {
    "comment_id",
    "ts_ms",
    "user_ref",
    "lang",
    "text",
    "label",
    "label_source",
    "has_phone",
    "has_link",
    "order",
}
KEYS |= {"situations"}
ORDER_KEYS = {"product", "size", "color", "qty", "reference_kind"}
ACTIONS = {"confirm_order", "ask_customer", "needs_seller", "confirm_cancel", "none"}
DIGIT_WORDS = {w: str(i) for i, w in enumerate("không một hai ba bốn năm sáu bảy tám chín".split())}
GOLD_PATH = Path(os.environ.get("C5_GOLD") or ROOT / "data" / "local" / "test1" / "labels.json")


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


STREAMS = {k: jsonl(C5 / f"stream-{k}.jsonl") for k in range(1, 6)}
MESSENGER = {k: jsonl(C5 / f"messenger-{k}.jsonl") for k in range(1, 6)}
WINDOWS = json.loads((C5 / "windows.json").read_text())
SEGMENTS = [s for s in WINDOWS["segments"] if s["product"]]
ALL = [r for rows in STREAMS.values() for r in rows]
ORDERS = [r for r in ALL if r["label"] == "order"]


def norm(text):
    return re.sub(r"[\s.,!?…]+$", "", " ".join(unicodedata.normalize("NFC", text).lower().split()))


def spelled_numbers(text):
    """Runs of 10 or more digits written as words ("không chín không không ...")."""
    runs, run = [], ""
    for word in [*re.split(r"[\s,:\-]+", text.lower()), ""]:
        if word in DIGIT_WORDS:
            run += DIGIT_WORDS[word]
            continue
        if len(run) >= 10:
            runs.append(run)
        run = ""
    return runs


def now_window(product, ts):
    """The window in which a comment about the product on screen is unambiguous: start + 8 s to end - 5 s."""
    return next(
        (s for s in SEGMENTS if s["product"] == product and s["from_ms"] + 8000 <= ts <= s["to_ms"] - 5000),
        None,
    )


def author():
    spec = importlib.util.spec_from_file_location("author_c5", C5 / "author_c5.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_five_streams_of_100_with_the_gold_fields():
    for k, rows in STREAMS.items():
        assert len(rows) == 100
        assert [r["comment_id"] for r in rows] == [f"c5-{k}-{n:03d}" for n in range(1, 101)]
        assert [r["ts_ms"] for r in rows] == sorted(r["ts_ms"] for r in rows)
        for r in rows:
            assert KEYS <= set(r) <= KEYS | {"expected_action", "arrives_ms"}, r
            assert r["label"] in LABELS and r["label_source"] == "template" and r["lang"] in ("vi", "en"), r
            assert re.fullmatch(rf"k{k}-u\d\d", r["user_ref"]), r
            assert isinstance(r["ts_ms"], int) and 0 <= r["ts_ms"] < LOOP_MS, r
            if "arrives_ms" in r:
                assert r["ts_ms"] < r["arrives_ms"] < LOOP_MS, r
            if r["label"] != "order":
                assert r["order"] is None, r
                continue
            o = r["order"]
            assert set(o) == ORDER_KEYS and r["expected_action"] in ACTIONS, r
            assert isinstance(o["qty"], int) and o["qty"] >= 1, r
            assert o["reference_kind"] in ("current_card", "previous_card", "unresolved"), r
            assert (o["product"] is None) == (o["reference_kind"] == "unresolved"), r
            assert o["product"] is None or re.fullmatch(r"SP(0[1-9]|10)", o["product"]), r
    assert sum(r["lang"] == "en" for r in ALL) < sum(r["lang"] == "vi" for r in ALL) / 4


def test_phone_numbers_follow_the_fake_pattern_and_match_the_gold():
    digits = words = 0
    for r in ALL:
        found = [m.group(0) for m in PHONE.finditer(r["text"])]
        spelled = spelled_numbers(r["text"])
        assert all(re.fullmatch(r"0900 000 0\d\d", n) for n in found), r
        assert all(re.fullmatch(r"09000000\d\d", n) for n in spelled), r
        assert r["has_phone"] == bool(found or spelled), r
        assert r["has_link"] == bool(LINK.search(r["text"])), r
        digits, words = digits + bool(found), words + bool(spelled)
    assert digits >= 5 and words >= 3


def test_no_text_repeats_across_streams_or_from_the_gold_200():
    gold = {norm(json.loads(line)["text"]) for line in (ROOT / "data" / "comments" / "labels.jsonl").open()}
    texts = [norm(r["text"]) for r in ALL]
    assert len(set(texts)) == len(texts) == 500
    assert not set(texts) & gold


def test_references_sit_inside_their_windows():
    for r in ORDERS:
        o, ts = r["order"], r["ts_ms"]
        if o["reference_kind"] == "current_card":
            window = now_window(o["product"], ts)
            assert window, r
            if "arrives_ms" in r:  # late: written while the item was on screen, arrives after it left
                assert r["arrives_ms"] > window["to_ms"], r
        elif o["reference_kind"] == "previous_card":  # within 60 s after the switch, the new item up for 8 s
            assert any(
                prev["product"] == o["product"]
                and s["from_ms"] + 8000 <= ts <= min(s["from_ms"] + 60000, s["to_ms"] - 5000)
                for prev, s in zip(SEGMENTS, SEGMENTS[1:], strict=False)
            ), r


def test_minimum_counts_per_situation():
    refs = [r for r in ORDERS if r["order"]["product"]]
    just_now = [
        r for r in refs if r["order"]["reference_kind"] == "previous_card" and "vừa nãy" in r["text"].lower()
    ]
    assert len(refs) >= 50 and len(just_now) >= 15  # 3
    assert sum(r["label"] == "stock_question" for r in ALL) >= 10  # 4
    assert sum("arrives_ms" in r for r in ORDERS) >= 5  # 5
    reads = Counter((cancel_request(r["text"]) or {}).get("kind") for r in ALL)
    assert reads["negated"] >= 2 and reads["cancel"] >= 2  # 7, 8
    replies = Counter(m["situation"] for lines in MESSENGER.values() for m in lines)
    assert replies[8] >= 2 and replies[10] >= 2 and replies[12] >= 1  # 8, 10, 12


def test_orders_intent_reads_cancel_and_discount_only_where_the_gold_says():
    for rows in STREAMS.values():
        for i, r in enumerate(rows):
            action, o = r.get("expected_action"), r["order"]
            kind = (cancel_request(r["text"]) or {}).get("kind")
            assert kind == {"confirm_cancel": "cancel", "none": "negated"}.get(action), r
            if kind:  # the customer of an earlier order in this loop
                assert any(
                    p["label"] == "order"
                    and p["user_ref"] == r["user_ref"]
                    and p["expected_action"] == "confirm_order"
                    for p in rows[:i]
                ), r
            asks_discount = bool(o) and action == "needs_seller" and o["qty"] == 1
            assert (discount_request(r["text"]) is not None) == asks_discount, r


def test_messenger_scripts_answer_the_customers_own_orders():
    expect = {"CONFIRMED": "yes", "NEEDS_SELLER": "yes", "CANCELLED": "no"}
    for k, lines in MESSENGER.items():
        rows = {r["comment_id"]: r for r in STREAMS[k]}
        assert [m["msg_id"] for m in lines] == [f"c5-{k}-m{n:02d}" for n in range(1, len(lines) + 1)]
        assert [m["not_before_ms"] for m in lines] == sorted(m["not_before_ms"] for m in lines)
        for m in lines:
            order, comment = rows[m["order_comment_id"]], rows[m["comment_id"]]
            assert order["label"] == "order" and order["expected_action"] == "confirm_order", m
            assert m["user_ref"] == order["user_ref"] == comment["user_ref"], m
            assert comment["ts_ms"] < m["not_before_ms"] < LOOP_MS, m
            assert (m["quick_reply"] is None) != (m["text"] is None), m
            payload = (
                {"quick_reply": f"{m['quick_reply']}:o-test"} if m["quick_reply"] else {"text": m["text"]}
            )
            assert customer_reply(None, payload)[0] == expect[m["expect"]], m
            if m["situation"] == 8:
                assert comment["expected_action"] == "confirm_cancel" and m["quick_reply"] == "CANCEL", m
            if m["situation"] == 10:  # ordered before the fixed price was said, "ok" after it
                p = m["after_price_change"]
                said = WINDOWS["price_changes"][p]
                assert order["order"]["product"] == p and m["text"] and m["not_before_ms"] > said[1], m
                assert any(
                    s["product"] == p and s["from_ms"] <= order["ts_ms"] < said[0] <= s["to_ms"]
                    for s in SEGMENTS
                ), m


def test_the_files_are_what_author_c5_writes():
    taken = [json.loads(line)["text"] for line in (ROOT / "data" / "comments" / "labels.jsonl").open()]
    out = author().generate(WINDOWS, taken)
    assert {k: rows for k, (rows, _) in out.items()} == STREAMS
    assert {k: lines for k, (_, lines) in out.items()} == MESSENGER


@pytest.mark.skipif(
    not GOLD_PATH.exists(), reason="local test1 gold not present (data/local/test1, not committed)"
)
def test_orders_fit_the_local_test1_stock_and_prices():
    gold = json.loads(GOLD_PATH.read_text())
    assert author().windows(gold) == WINDOWS
    products = gold["products"]
    for k, rows in STREAMS.items():
        held = Counter()
        for r in rows:
            o = r["order"]
            if not o or not o["product"]:
                continue
            stock = products[o["product"]]["stock"]
            if r["expected_action"] == "ask_customer":  # no size given for an item that has several
                assert o["size"] is None and len(stock) > 1, r
                continue
            size = o["size"] or "one"
            assert size in stock, r
            if o["qty"] > 1:  # asks for more than there is: the seller decides
                assert r["expected_action"] == "needs_seller" and o["qty"] > stock[size], r
            else:
                held[o["product"], size] += 1
                assert products[o["product"]]["price_first"] <= 2_000_000, r
        for (p, size), n in held.items():
            assert n <= products[p]["stock"][size], (k, p, size, n)
    for m in (m for lines in MESSENGER.values() for m in lines if m["situation"] == 10):
        assert (
            products[m["after_price_change"]]["price_first"]
            != products[m["after_price_change"]]["price_fixed"]
        )
