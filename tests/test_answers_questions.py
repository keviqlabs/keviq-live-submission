import pytest

from answers.questions import Question, parse


@pytest.mark.parametrize(
    ("text", "label", "want"),
    [
        ("Size 42 còn không shop", "stock_question", Question("stock", "42")),
        ("40 còn không shop ơi", "stock_question", Question("stock", "40")),
        ("Còn 42 không vậy shop", "stock_question", Question("stock", "42")),
        ("do you have size 40?", "stock_question", Question("stock", "40")),
        ("Shop ơi size L còn mấy cái", "stock_question", Question("stock", "L")),
        ("Em hỏi size XL còn không ạ", "stock_question", Question("stock", "XL")),
        ("Cái này còn hàng không shop", "stock_question", Question("stock", None)),
        ("Hết hàng chưa shop ơi", "stock_question", Question("stock", None)),
        ("Bao nhiêu tiền 1 đôi vậy shop", "price_question", Question("price")),
        ("Mua 1 đôi hết bao nhiêu tiền", "price_question", Question("price")),
        ("Giá lẻ 1 cái bao nhiêu shop", "price_question", Question("price")),
        ("Giá có bao ship chưa ạ", "price_question", Question("policy")),
        ("Mua 2 đôi có giá tốt hơn không shop", "price_question", Question("policy")),
        ("Giá sỉ cái này sao shop", "price_question", Question("policy")),
        ("Giá trên thẻ là giá cuối chưa shop", "price_question", Question("policy")),
        ("Lấy đôi này cho em nhé", "order", Question("ask_size")),
        ("I want this one", "order", Question("ask_size")),
        ("Chốt 1 cái này nha shop", "order", Question("ask_size")),
    ],
)
def test_label_first(text, label, want):
    assert parse(text, label) == want


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Chốt đôi này size 40 nha shop", "order"),
        ("One in size M please", "order"),
        ("Cho em đôi vừa nãy size 41 nha", "order"),
        ("Em muốn cái ban nãy ạ", "order"),
        ("Lấy cho em cái túi đeo chéo hôm qua nha", "order"),
        ("I'll take the scarf from last week's live", "order"),
        ("Lấy 1 cái size L, bớt cho em 20k nha", "order"),
        ("Lấy 1 cái, bớt cho em 20k nha", "order"),
        ("Inbox mình nhận quà 200k miễn phí", "scam"),
        ("Mic rè quá shop", "complaint"),
        ("Shop live mấy giờ tới", "other"),
    ],
)
def test_not_a_current_item_question(text, label):
    assert parse(text, label) is None


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Size 40 còn không shop", Question("stock", "40")),
        ("is size 41 still available?", Question("stock", "41")),
        ("Còn mấy cái vậy shop", Question("stock", None)),
        ("how much is this one?", Question("price")),
        ("Mua 1 đôi hết bao nhiêu tiền", Question("price")),
        ("Ship về Cần Thơ mất phí bao nhiêu", Question("policy")),
        ("Lấy đôi này cho em nhé", Question("ask_size")),
        ("Lấy đôi này size 40 cho chị", None),
        ("Đơn bị giữ, thanh toán phí tại https://voucher.example/pay", None),
        ("Bạn trúng quà, gọi 0900 000 011 để nhận", None),
        ("Phí ship lần trước tính sai", None),
        ("Mọi người ơi, chào cả nhà", None),
    ],
)
def test_text_only(text, want):
    assert parse(text) == want
