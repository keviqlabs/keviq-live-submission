"""Reading the customer's words for the order flow: discount requests in a comment, yes/no in a reply.

comment_labeled has no discount field (v1), so the orders stream reads the comment text itself. This only
decides *whether to ask the seller*; it never sets a price (orders/rules.py does, with the seller).
"""

import re
import unicodedata

DISCOUNT_WORDS = re.compile(r"\b(giảm|bớt|bot|rẻ hơn|re hon|discount|sale|fix giá|fix gia)\b", re.I)
UNIT = r"\s*(k|nghìn|ngàn|ngan|nghin|tr|triệu|trieu|%)?(\d{1,3})?"  # "1tr2" = 1,200,000
TARGET = re.compile(r"\b(?:còn|con|giá|gia|chỉ|chi)\s+(\d[\d.,]*)" + UNIT, re.I)
REDUCTION = re.compile(r"\b(?:giảm|bớt|bot)\s+(\d[\d.,]*)" + UNIT, re.I)

YES = {"ok", "oke", "okie", "dong y", "chot", "yes", "xac nhan", "co", "uh", "u", "dc", "duoc"}
NO = {"huy", "khong", "ko", "thoi", "no", "cancel"}


def vnd(number: str, unit: str | None, tail: str | None = None) -> int | None:
    digits = number.replace(".", "").replace(",", "")
    if not digits.isdigit():
        return None
    n = int(digits)
    unit = (unit or "").lower()
    if unit in ("tr", "triệu", "trieu"):
        return n * 1_000_000 + (int(tail) * 10 ** (6 - len(tail)) if tail else 0)
    if unit or n < 10_000:  # "400", "400k", "400 nghìn" mean thousands in a live-sale comment
        return n * 1000
    return n


def discount_request(text: str | None) -> dict | None:
    """{"kind": "target" | "reduction" | "unspecified", "value": int | None, "phrase": str} or None."""
    if not text:
        return None
    m = REDUCTION.search(text)
    if m and m.group(2) != "%":
        return {"kind": "reduction", "value": vnd(*m.groups()), "phrase": m.group(0).strip()}
    word = DISCOUNT_WORDS.search(text)
    if not word:
        return None
    m = TARGET.search(text)
    if m and m.group(2) != "%":
        start = word.start() if 0 <= m.start() - word.end() <= 3 else m.start()
        return {"kind": "target", "value": vnd(*m.groups()), "phrase": text[start : m.end()].strip()}
    return {"kind": "unspecified", "value": None, "phrase": word.group(0)}


def fold(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def customer_reply(template: str | None, payload: dict) -> tuple[str | None, str | None]:
    """('yes' | 'no' | None, order_id | None) from a message_in: template, quick reply, or a short exact text."""
    order_id = payload.get("order_id")
    if template == "confirm_yes":
        return "yes", order_id
    if template == "confirm_no":
        return "no", order_id
    kind, _, qr_order = (payload.get("quick_reply") or "").partition(":")
    if kind in ("CONFIRM", "CANCEL") and qr_order:
        return ("yes" if kind == "CONFIRM" else "no"), qr_order
    words = fold(payload.get("text") or "")
    if words in YES:
        return "yes", order_id
    if words in NO:
        return "no", order_id
    return None, order_id
