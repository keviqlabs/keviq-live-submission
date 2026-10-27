"""Reading the customer's words for the order flow: discount and cancel requests in a comment, yes/no/keep in
a reply.

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
QUICK_REPLIES = {"CONFIRM": "yes", "CANCEL": "no", "KEEP": "keep", "PICK": "pick"}

# Read on the lowercased text with its diacritics: "huỷ"/"hủy" is cancel, the name "Huy" and "chuyển" are not.
# Without diacritics only "huy đơn" counts. Punctuation is kept, so "không, huỷ đi" is a cancel.
HUY = r"\b(?:huỷ|hủy|huy(?=\s+(?:đơn|don)\b))"
CANCEL_WORDS = re.compile(
    HUY + r"|\bcancel\b|\bbỏ\s+(?:đơn|don)\b"
    r"|\b(?:không|khong|ko|k)\s+(?:lấy|lay|mua)\s+(?:nữa|nua)\b"
    r"|\bthôi\s+(?:không|khong|ko|k)\s+(?:lấy|lay|mua)\b"
)
NEGATED = re.compile(
    r"\b(?:đừng|dung|không|khong|ko|k|khỏi|khoi|chẳng|chang)\s+"
    r"(?:cần\s+|can\s+|phải\s+|phai\s+|có\s+|co\s+|muốn\s+|muon\s+)?"
    r"(?:" + HUY + r"|cancel\b|bỏ\s+(?:đơn|don)\b)"
)
UNCLEAR = re.compile(  # a link, a passive "bị huỷ", "chưa huỷ", or a hedge: not the customer asking to cancel
    r"https?://|\bwww\.|\b(?:bị|bi|chưa|chua)\s+"
    + HUY.removeprefix(r"\b")
    + r"|\b(?:hay|nếu|neu|không biết|khong biet|phân vân|phan van|suy nghĩ|suy nghi|có nên|co nen)\b"
)


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


def cancel_request(text: str | None) -> dict | None:
    """{"kind": "cancel" | "negated" | "unclear", "phrase": str} or None. Only "cancel" may lead to a question to
    the customer; nothing here cancels an order (that takes the customer's CANCEL:<order_id> quick reply)."""
    t = unicodedata.normalize("NFC", text or "").lower()
    word = CANCEL_WORDS.search(t)
    if not word:
        return None
    negated = NEGATED.search(t)
    if negated:
        return {"kind": "negated", "phrase": negated.group(0)}
    if UNCLEAR.search(t):
        return {"kind": "unclear", "phrase": word.group(0)}
    return {"kind": "cancel", "phrase": word.group(0)}


def fold(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def customer_reply(template: str | None, payload: dict) -> tuple[str | None, str | None]:
    """('yes' | 'no' | 'keep' | 'pick' | None, order_id | None) from a message_in: template, quick reply, or a
    short exact text. A plain-text 'no' carries no order_id unless the payload names one. 'pick' is the answer to
    an ask_variant question (PICK:<order_id>#<i>); orders/variants.py reads which variant."""
    order_id = payload.get("order_id")
    if template == "confirm_yes":
        return "yes", order_id
    if template == "confirm_no":
        return "no", order_id
    if template == "keep_order":
        return "keep", order_id
    if template == "pick_variant":
        return "pick", order_id
    kind, _, qr_order = (payload.get("quick_reply") or "").partition(":")
    if kind in QUICK_REPLIES and qr_order:
        return QUICK_REPLIES[kind], qr_order.partition("#")[0]
    words = fold(payload.get("text") or "")
    if words in YES:
        return "yes", order_id
    if words in NO:
        return "no", order_id
    return None, order_id
