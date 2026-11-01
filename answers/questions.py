"""What a viewer comment asks about the item on screen: its price, a policy, its stock, or an order with no size.

The comment's label (comment_labeled) is used first; the text decides the kind and the size. Without a label the
text alone decides. Anything about another item (the one before, an older live), a discount or a link or phone
number is not a question answered here and returns None. Standard library only, so it runs without voice.
"""

import re
import unicodedata
from dataclasses import dataclass

KINDS = ("price", "policy", "stock", "ask_size")


@dataclass(frozen=True)
class Question:
    kind: str
    size: str | None = None


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).lower().split())


def _any(words, t: str) -> bool:
    return any(re.search(rf"(?<!\w){w}(?!\w)", t) for w in words)


OTHER_ITEM = ("vừa nãy", "hồi nãy", "lúc nãy", "ban nãy", "the one before", "hôm qua", "hôm trước", "hôm bữa",
              "tuần rồi", "tuần trước", "live trước", "lần trước", "buổi trước", "bài đăng", "last week", "yesterday")  # fmt: skip
DISCOUNT = ("bớt", "giảm", "discount", "rẻ hơn")
POLICY = ("ship", "freeship", "phí", "sỉ", "hộp", "giá cuối", "giá tốt", "mua 2", "bao ship", "tổng tiền",
          "shipping", "wholesale", "discount", "bớt", "giảm")  # fmt: skip
PRICE = ("giá", "bao nhiêu", "nhiêu", "mấy trăm", "how much", "price", "bao tiền")
STOCK = ("còn hàng", "hết hàng", "còn size", "còn mấy", "còn không", "hết chưa", "còn nhiều", "có sẵn", "còn .{1,12} không",
         "in stock", "available", "do you have", "còn .{1,12} chứ")  # fmt: skip
ORDER = ("chốt", "lấy", "đặt", "lên đơn", "ghi đơn", "ghi cho", "mua", "cho mình", "cho em 1", "cho chị", "để em",
         "để cho", "i want", "i'll take", "i'll get", "one of these", "order")  # fmt: skip
_SIZE_WORD = re.compile(r"(?<!\w)(?:size|sz|cỡ)\s*(\d{2}|xxl|xl|s|m|l)(?!\w)")
_SIZE_BARE = re.compile(r"(?<![\w.,])(3[4-9]|4[0-6])(?![\w.,]|\s*(?:k|nghìn|ngàn|đ)(?!\w))")
_SIZE_LETTER = re.compile(r"(?<!\w)(XXL|XL|S|M|L)(?!\w)")
_LINK = re.compile(r"https?://|www\.|\.example|\d{3,4}\s*\d{3}\s*\d{3}")


def size_of(text: str) -> str | None:
    t = _norm(text)
    m = _SIZE_WORD.search(t) or _SIZE_BARE.search(t)
    if m:
        return m.group(1).upper()
    m = _SIZE_LETTER.search(unicodedata.normalize("NFC", text))
    return m.group(1) if m else None


def _price_kind(t: str) -> str:
    return "policy" if _any(POLICY, t) else "price"


def parse(text: str, label: str | None = None) -> Question | None:
    t = _norm(text)
    if _LINK.search(t) or _any(OTHER_ITEM, t):
        return None
    if label == "stock_question":
        return Question("stock", size_of(text))
    if label == "price_question":
        return Question(_price_kind(t))
    if label == "order":
        if size_of(text) or _any(DISCOUNT, t):
            return None
        return Question("ask_size")
    if label is not None:
        return None
    # No label: the text alone decides; a price word wins over "hết" ("hết bao nhiêu tiền").
    if _any(PRICE, t):
        return Question(_price_kind(t))
    if _any(STOCK, t):
        return Question("stock", size_of(text))
    if _any(POLICY, t):
        return None
    if _any(ORDER, t) and not size_of(text) and not _any(DISCOUNT, t):
        return Question("ask_size")
    return None
