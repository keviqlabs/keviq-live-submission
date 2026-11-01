import ast
import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from answers.facts import facts_from_card
from answers.reply import Answer, check
from answers.selector import Decision
from measure import answers_eval as ev

ROOT = Path(__file__).resolve().parents[1]
ANSWER_MODULES = ("questions", "facts", "reply", "selector")


def test_answer_modules_need_only_the_standard_library_and_each_other():
    allowed = {"re", "unicodedata", "dataclasses"}
    for name in ANSWER_MODULES:
        tree = ast.parse((ROOT / f"answers/{name}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(n.name in allowed for n in node.names), name
            if isinstance(node, ast.ImportFrom):
                ok = node.module in ANSWER_MODULES if node.level == 1 else node.module in allowed
                assert ok and node.level in (0, 1), (name, node.module)


def test_eval_set_is_what_the_builder_writes():
    r = subprocess.run([sys.executable, "data/answers/build_eval.py", "--check"], cwd=ROOT, capture_output=True,
                       text=True, timeout=60)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_eval_card_is_a_valid_card_event():
    schema = json.loads((ROOT / "contracts/events/card.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema)
    cards = [r["payload"] for rows in ev.load().values() for r in rows if r["type"] == "card"]
    assert len(cards) > 50
    for c in cards:
        v.validate(c)


def test_eval_set_has_every_group_and_its_traps():
    groups = {r["gold"]["category"] for rows in ev.load().values() for r in rows if r["type"] == "comment"}
    assert set(ev.CATEGORIES) == groups


def test_no_wrong_price_stock_or_variant_with_labels_or_from_text():
    loops = ev.load()
    labelled = ev.score(loops, labels=True)
    assert not [r for r in labelled["results"] if r["outcome"] == "wrong"]
    assert labelled["gate"][0]["passed_wrong"] == {}
    text_only = ev.score(loops, labels=False)
    assert not ev.wrong_facts(text_only["results"])


def test_rescore_cases_size_and_stock():
    r = ev.rescore(ev.load())
    assert len(r["size_ids"]) == 4 and len(r["stock_ids"]) == 13

    def count(mode, group, outcome):
        return sum(1 for o, d, _ in r[mode][group].values() if (d if o == "correct" else o) == outcome)

    assert count("a", "size", "answered") == 3 and count("a", "size", "silent_not_known") == 1
    assert count("a", "stock", "answered") == 6 and count("a", "stock", "deferred") == 7
    assert count("b", "size", "answered") == 4 and count("b", "stock", "answered") == 13
    for mode in ("a", "b"):
        for group in ("size", "stock"):
            assert count(mode, group, "wrong") == 0 and count(mode, group, "no_answer") == 0


def _loop_one():
    rows = ev.load()[1]
    truth = ev.Truth(rows)
    t = 30000  # SP01 on screen, approved card v2 (250 000, size 40: 2, size 41: 1)
    card = max((r["payload"] for r in rows if r["type"] == "card" and r["item"] == "SP01" and r["t_ms"] <= t),
               key=lambda p: p["version"])  # fmt: skip
    return truth, t, card


def _said(text, card, t, kind="price", action="answer"):
    return Decision(t, ("c",), kind, None, action, text, card["card_id"], card["version"], kind, t - 4000)


def test_scorer_catches_planted_wrong_sentences():
    truth, t, card = _loop_one()
    assert card["price"] == 250000
    price = {"kind": "price", "size": None, "item": "SP01", "category": "price", "expect": "answer"}
    stock = {"kind": "stock", "size": "40", "item": "SP01", "category": "stock", "expect": "answer"}
    other = {"kind": None, "size": None, "item": None, "category": "not_a_question", "expect": "silent"}
    cases = [
        (price, "Giày đen giá 250 nghìn đồng ạ.", ("correct", "answered")),
        (price, "Giày đen giá 260 nghìn đồng ạ.", ("wrong", "wrong_price")),
        (price, "Giày đen giá 250.000 đồng, giảm còn 5 ạ.", ("wrong", "wrong_stock")),
        (price, "Giày trắng giá 250 nghìn đồng ạ.", ("wrong", "wrong_item")),
        (stock, "Giày đen size 40 còn 3 ạ.", ("wrong", "wrong_stock")),
        (stock, "Giày đen size 44 còn 2 ạ.", ("wrong", "wrong_variant")),
        (stock, "Giày đen size 41 còn 1 ạ.", ("wrong", "wrong_variant")),
        (stock, "Giày đen size 40 còn 2 ạ.", ("correct", "answered")),
        (stock, "Số lượng giày đen để người bán xác nhận giúp bạn nhé.", ("no_answer", "deferred_known")),
        (other, "Giày đen giá 250 nghìn đồng ạ.", ("wrong", "spoke_on_negative")),
    ]
    for gold, text, want in cases:
        assert ev.judge(gold, _said(text, card, t, gold["kind"] or "price"), truth, t - 4000) == want, text


def test_scorer_judges_against_the_item_really_on_screen():
    truth, _, card = _loop_one()
    gold = {"kind": "price", "size": None, "item": "SP01", "category": "price", "expect": "answer"}
    t = 58600 + 500  # SP02 is on screen, its card arrives 1.5 s after the switch
    assert ev.judge(gold, _said("Giày đen giá 250 nghìn đồng ạ.", card, t), truth, t - 4000) == (
        "wrong",
        "after_switch",
    )


def test_gate_drops_planted_wrong_sentences():
    _, t, card = _loop_one()
    now = facts_from_card(card)
    for text, reason in (("Giày đen giá 260 nghìn đồng ạ.", "wrong_price"),
                         ("Giày đen size 40 còn 3 ạ.", "wrong_stock"),
                         ("Giày đen size 44 còn 2 ạ.", "wrong_variant")):  # fmt: skip
        assert check(Answer("answer", text, card["card_id"], card["version"], "stock", ""), now) == reason
