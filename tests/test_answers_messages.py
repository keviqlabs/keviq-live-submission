"""The private-reply scorer: the path that runs, judged by the same independent judge as the eval."""

from collections import Counter

import pytest

from measure import answers_messages as am
from measure.answers_eval import load, rescore, wrong_facts


@pytest.fixture(scope="module")
def loops():
    return load()


def test_no_wrong_price_stock_or_variant_at_any_reply_moment(loops):
    for delay in am.SWEEP_MS:
        assert wrong_facts(am.score(loops, delay)["results"]).total() == 0, delay


def test_price_and_stock_questions_get_a_reply(loops):
    res = am.score(loops)["results"]
    by = Counter((r["category"], r["detail"]) for r in res if r["replied"])
    assert by[("price", "answered")] == 46 and by[("policy", "deferred")] == 19
    assert by[("stock", "answered")] + by[("stock", "deferred")] == 65
    # an order without a size is the order flow's question, not this path's
    assert not any(
        r["replied"] for r in res if r["category"] == "ask_size" and r["reason"] == "not_a_question"
    )


def test_reply_built_after_the_new_card_is_stored_is_dropped(loops):
    res = am.score(loops, 5000)["results"]
    switched = [r for r in res if r["category"] == "trap_switch_before_answer"]
    assert switched and all(r["reason"] == "item_changed" and not r["replied"] for r in switched)


def test_rescore_every_stock_question_gets_a_reply_with_its_comment_id(loops):
    r = rescore(loops, runner=am.run_messages)
    for mode in ("a", "b"):
        stock = r[mode]["stock"]
        assert len(stock) == 13
        assert all(
            outcome == "correct" and detail in ("answered", "deferred")
            for outcome, detail, _ in stock.values()
        )
        assert not any(outcome == "wrong" for outcome, _, _ in r[mode]["size"].values())
    assert Counter(d for _, d, _ in r["a"]["stock"].values()) == {"answered": 6, "deferred": 7}
    assert all("để người bán xác nhận" in said for _, d, said in r["a"]["stock"].values() if d == "deferred")


def test_scorer_counts_a_planted_wrong_price(loops, monkeypatch):
    real = am.on_question

    async def planted(label, events, messenger, now):
        partials, reason = await real(label, events, messenger, now)
        for p in partials:
            p["payload"]["text"] = p["payload"]["text"].replace(" nghìn đồng", "1 nghìn đồng")
        return partials, reason

    monkeypatch.setattr(am, "on_question", planted)
    assert wrong_facts(am.score(loops)["results"]).total() > 0
