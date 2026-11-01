import pytest

from answers.facts import facts_from_card
from answers.questions import Question
from answers.reply import Answer, check, compose, money


def card(
    sku="SP01", name="SP01 giày đen", price=250000, status="live", version=2, variants=None, card_id="c1"
):
    if variants is None:
        variants = [{"size": "40", "color": "đen", "stock": 2}, {"size": "41", "color": "đen", "stock": 1}]
    return {
        "event_id": f"{card_id}.v{version}", "session_id": "test", "ts_ms": 1, "source": "replay", "v": 1,
        "type": "card", "card_id": card_id, "version": version, "sku": sku, "name": name, "price": price,
        "currency": "VND", "variants": variants, "stock": sum(v["stock"] for v in variants), "image_url": None,
        "status": status, "provenance": {"frame_ts_ms": 1000, "speech_event_ids": [], "edited_by": None},
    }  # fmt: skip


SHOES = facts_from_card(card())
PAD = facts_from_card(
    card("SP07", "SP07 tay cầm đen", 450000, variants=[{"size": None, "color": "đen", "stock": 2}])
)
NO_SKU = facts_from_card(card(None, "bàn phím đen", 650000, variants=[]))
DRAFT = facts_from_card(card(price=None, status="draft", version=1))
SOLD_40 = facts_from_card(
    card(status="edited", version=3, variants=[{"size": "40", "color": "đen", "stock": 0},
                                               {"size": "41", "color": "đen", "stock": 1}])
)  # fmt: skip


def test_facts():
    assert SHOES.approved and SHOES.price == 250000 and SHOES.stock == (("40", 2), ("41", 1))
    assert not DRAFT.approved
    assert NO_SKU.approved and NO_SKU.stock is None
    two_colours = facts_from_card(card(variants=[{"size": "40", "color": "đen", "stock": 2},
                                                 {"size": "40", "color": "trắng", "stock": 1}]))  # fmt: skip
    assert two_colours.stock is None
    assert two_colours.for_color("đen").stock == (("40", 2),)
    assert two_colours.for_color("trắng").stock == (("40", 1),)
    assert not facts_from_card(card(price=0)).approved
    assert not facts_from_card(card(status="retired")).approved


@pytest.mark.parametrize(
    ("vnd", "want"),
    [(250000, "250 nghìn đồng"), (1000000, "1 triệu đồng"), (1250000, "1 triệu 250 nghìn đồng"),
     (249500, "249.500 đồng")],
)  # fmt: skip
def test_money(vnd, want):
    assert money(vnd) == want


@pytest.mark.parametrize(
    ("q", "facts", "action", "text"),
    [
        (Question("price"), SHOES, "answer", "Giày đen giá 250 nghìn đồng ạ."),
        (Question("stock", "40"), SHOES, "answer", "Giày đen size 40 còn 2 ạ."),
        (Question("stock", "40"), SOLD_40, "answer", "Giày đen size 40 hết hàng rồi ạ."),
        (Question("stock", None), SHOES, "answer", "Giày đen size 40 còn 2, size 41 còn 1 ạ."),
        (Question("stock", None), SOLD_40, "answer", "Giày đen size 40 hết hàng, size 41 còn 1 ạ."),
        (Question("stock", None), PAD, "answer", "Tay cầm đen còn 2 ạ."),
        (Question("stock", "42"), SHOES, "defer", "Size này để người bán xác nhận giúp bạn nhé."),
        (
            Question("stock", "40"),
            NO_SKU,
            "defer",
            "Số lượng bàn phím đen để người bán xác nhận giúp bạn nhé.",
        ),
        (Question("stock", None), DRAFT, "defer", "Số lượng món này để người bán xác nhận giúp bạn nhé."),
        (Question("price"), DRAFT, "defer", "Giá món này để người bán xác nhận giúp bạn nhé."),
        (Question("policy"), SHOES, "defer", "Câu này để người bán trả lời bạn nhé."),
        (Question("ask_size"), SHOES, "ask", "Bạn lấy size nào ạ? Giày đen còn size 40, size 41."),
        (Question("ask_size"), SOLD_40, "ask", "Bạn lấy size nào ạ? Giày đen còn size 41."),
        (Question("ask_size"), PAD, "silent", ""),
        (Question("ask_size"), NO_SKU, "silent", ""),
        (Question("ask_size"), DRAFT, "silent", ""),
        (Question("price"), None, "silent", ""),
    ],
)
def test_compose(q, facts, action, text):
    a = compose(q, facts)
    assert (a.action, a.text) == (action, text)
    if facts is not None and action != "silent":
        assert (a.card_id, a.version, a.kind) == (facts.card_id, facts.version, q.kind)
        assert check(a, facts) is None


def test_unknown_stock_never_states_a_number():
    for q in (Question("stock", "40"), Question("stock", None)):
        a = compose(q, NO_SKU)
        assert "để người bán xác nhận" in a.text and not any(c.isdigit() for c in a.text)


def _answer(text, facts=SHOES, kind="price", action="answer"):
    return Answer(action, text, facts.card_id, facts.version, kind, "test")


@pytest.mark.parametrize(
    ("text", "facts", "kind", "reason"),
    [
        ("Giày đen giá 260 nghìn đồng ạ.", SHOES, "price", "wrong_price"),
        ("Giày đen giá 250k ạ, giảm 10% nữa.", SHOES, "price", "unexplained_number"),
        ("Giày đen giá hai trăm năm mươi nghìn đồng ạ.", SHOES, "price", "spelled_number"),
        ("Giá 250 nghìn đồng ạ.", SHOES, "price", "name_missing"),
        ("Giày đen đẹp lắm ạ.", SHOES, "price", "fact_missing"),
        ("Giày đen size 40 còn 3 ạ.", SHOES, "stock", "wrong_stock"),
        ("Giày đen size 42 còn 1 ạ.", SHOES, "stock", "wrong_variant"),
        ("Giày đen còn size 42.", SHOES, "stock", "wrong_variant"),
        ("Giày đen size 40 hết hàng rồi ạ.", SHOES, "stock", "wrong_stock"),
        ("Giày đen hết hàng rồi ạ.", SOLD_40, "stock", "wrong_stock"),
        ("Bàn phím đen còn 2 ạ.", NO_SKU, "stock", "stock_unknown"),
        ("Giày đen giá 250 nghìn đồng ạ.", DRAFT, "price", "not_approved"),
    ],
)
def test_check_rejects(text, facts, kind, reason):
    assert check(_answer(text, facts, kind), facts) == reason


def test_check_accepts_other_money_forms():
    for text in ("Giày đen giá 250.000 đồng ạ.", "Giày đen giá 250k ạ.", "Giày đen 250 nghìn ạ."):
        assert check(_answer(text), SHOES) is None


def test_check_against_current_state():
    a = compose(Question("price"), SHOES)
    edited = facts_from_card(card(price=230000, status="edited", version=3))
    other = facts_from_card(card(card_id="c2", sku="SP02", name="SP02 giày trắng", price=320000))
    assert check(a, edited) == "version_changed"
    assert check(a, other) == "item_changed"
    assert check(a, None) == "no_card"
    assert check(a, SHOES) is None


def test_check_model_text_against_facts():
    a = compose(Question("stock", "40"), SHOES)
    assert check(a, SHOES, text="Dạ giày đen size 40 vẫn còn 2 đôi nha.") is None
    assert check(a, SHOES, text="Dạ giày đen size 40 còn hai đôi nha.") == "spelled_number"
