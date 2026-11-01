"""The reply to one question about the item on screen, and the check that its facts still hold.

compose() writes the sentence from approved facts only: a price, a stock count per size, or the sizes still in
stock when an order has no size. Without approved facts it says the seller will confirm, or stays silent; it never
guesses. check() re-reads any text (a template or a model's wording) against the card state of that moment: same
card and version, every amount equal to the approved price, every size and count equal to the approved stock, "sold
out" only for a size at 0, no number it cannot explain and no number in words. Standard library only.
"""

import re
import unicodedata
from dataclasses import dataclass

from .facts import Facts, display_name
from .questions import Question

SELLER_CONFIRMS = "để người bán xác nhận giúp bạn nhé."


@dataclass(frozen=True)
class Answer:
    action: str  # answer | ask | defer | silent
    text: str
    card_id: str | None
    version: int | None
    kind: str
    reason: str
    color: str | None = None


def money(vnd: int) -> str:
    if vnd % 1000:
        return f"{vnd:,}".replace(",", ".") + " đồng"
    million, thousand = divmod(vnd // 1000, 1000)
    parts = ([f"{million} triệu"] if million else []) + ([f"{thousand} nghìn"] if thousand else [])
    return " ".join(parts) + " đồng"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _count(size: str | None, n: int) -> str:
    head = f"size {size} " if size is not None else ""
    return head + (f"còn {n}" if n > 0 else "hết hàng")


def compose(q: Question, facts: Facts | None) -> Answer:
    def out(action, text, reason):
        card_id, version = (facts.card_id, facts.version) if facts else (None, None)
        return Answer(action, text, card_id, version, q.kind, reason, q.color)

    if facts is None:
        return out("silent", "", "no_card")
    if q.kind in ("stock", "ask_size"):
        facts = facts.for_color(q.color)
    name = display_name(facts.name)
    if q.kind == "policy":
        return out("defer", "Câu này để người bán trả lời bạn nhé.", "policy")
    if not facts.approved:
        if q.kind == "price":
            return out("defer", "Giá món này " + SELLER_CONFIRMS, "not_approved")
        if q.kind == "stock":
            return out("defer", "Số lượng món này " + SELLER_CONFIRMS, "not_approved")
        return out("silent", "", "not_approved")
    if q.kind == "price":
        return out("answer", f"{_cap(name)} giá {money(facts.price)} ạ.", "price")
    if q.kind == "stock":
        if facts.stock is None:
            return out("defer", f"Số lượng {name} " + SELLER_CONFIRMS, "stock_unknown")
        if q.size is not None:
            n = facts.stock_of(q.size)
            if n is None:
                return out("defer", "Size này " + SELLER_CONFIRMS, "size_not_on_card")
            text = f"{_cap(name)} {_count(q.size, n)}" + (" ạ." if n > 0 else " rồi ạ.")
            return out("answer", text, "stock_size")
        if not facts.sized:
            n = facts.total
            return out("answer", f"{_cap(name)} {_count(None, n)}" + (" ạ." if n > 0 else " rồi ạ."), "stock")
        text = ", ".join(_count(s, n) for s, n in facts.stock)
        return out("answer", f"{_cap(name)} {text} ạ.", "stock_all")
    if q.kind == "ask_size":
        if facts.stock is None:
            return out("silent", "", "stock_unknown")
        if not facts.sized:
            return out("silent", "", "single_variant")
        left = [s for s, n in facts.stock if n > 0]
        if not left:
            return out("answer", f"{_cap(name)} hết hàng rồi ạ.", "sold_out")
        sizes = ", ".join(f"size {s}" for s in left)
        return out("ask", f"Bạn lấy size nào ạ? {_cap(name)} còn {sizes}.", "ask_size")
    return out("silent", "", "unknown_kind")


# --- check -------------------------------------------------------------------------------------------------------

_SIZE = r"(\d{2}|xxl|xl|s|m|l)"
_MONEY = [
    (re.compile(r"(\d+)\s*triệu(?:\s*(\d+)\s*(?:nghìn|ngàn))?(?:\s*(?:đồng|đ))?(?!\w)"),
     lambda m: int(m[1]) * 1_000_000 + int(m[2] or 0) * 1000),
    (re.compile(r"(\d{1,3}(?:\.\d{3})+)\s*(?:đồng|đ|vnd)(?!\w)"), lambda m: int(m[1].replace(".", ""))),
    (re.compile(r"(\d+)\s*(?:nghìn|ngàn|k)(?:\s*(?:đồng|đ))?(?!\w)"), lambda m: int(m[1]) * 1000),
    (re.compile(r"(\d+)\s*(?:đồng|vnd)(?!\w)"), lambda m: int(m[1])),
]  # fmt: skip
_PAIR = re.compile(
    rf"(?<!\w)size\s+{_SIZE}\s+(?:(?:vẫn\s+)?còn\s+(\d+)|(?:đã\s+)?hết(?:\s+hàng)?|không\s+còn)(?!\w)"
)
_SIZE_ONLY = re.compile(rf"(?<!\w)size\s+{_SIZE}(?!\w)")
_TOTAL = re.compile(r"(?<!\w)(?:vẫn\s+)?còn\s+(\d+)(?!\w)")
_SOLD_OUT = re.compile(r"(?<!\w)(?:hết(?:\s+hàng)?|không\s+còn|sold out|out of stock)(?!\w)")
_IN_STOCK = re.compile(r"(?<!\w)(?:còn\s+hàng|in stock)(?!\w)")
_SPELLED = {"một", "hai", "ba", "bốn", "tư", "năm", "lăm", "sáu", "bảy", "bẩy", "tám", "chín", "mười", "mươi",
            "trăm", "nghìn", "ngàn", "triệu", "chục", "linh", "lẻ", "one", "two", "three", "four", "five", "six",
            "seven", "eight", "nine", "ten", "hundred", "thousand", "million"}  # fmt: skip


def _cut(t: str, m: re.Match) -> str:
    return t[: m.start()] + " " * (m.end() - m.start()) + t[m.end() :]


def check(answer: Answer, now: Facts | None, text: str | None = None) -> str | None:
    """None when the text may be said now; otherwise the reason it must be dropped."""
    if now is None:
        return "no_card"
    if answer.card_id != now.card_id:
        return "item_changed"
    if answer.version != now.version:
        return "version_changed"
    if answer.kind in ("stock", "ask_size"):
        now = now.for_color(answer.color)
    t = " ".join(unicodedata.normalize("NFC", answer.text if text is None else text).lower().split())
    name = display_name(now.name).lower()
    rest = t.replace(name, " " * len(name)) if name else t
    found = {"money": 0, "stock": 0, "size": 0}
    for pattern, value in _MONEY:
        while m := pattern.search(rest):
            if not now.approved:
                return "not_approved"
            if value(m) != now.price:
                return "wrong_price"
            found["money"] += 1
            rest = _cut(rest, m)
    for m in list(_PAIR.finditer(rest)) + list(_SIZE_ONLY.finditer(rest)):
        if rest[m.start() : m.end()].strip() == "":
            continue
        if not now.approved:
            return "not_approved"
        if now.stock is None:
            return "stock_unknown"
        size = m[1].upper()
        have = now.stock_of(size)
        if have is None:
            return "wrong_variant"
        said = int(m[2]) if m.re is _PAIR and m[2] else (0 if m.re is _PAIR else None)
        if said is None and have == 0 or said is not None and said != have:
            return "wrong_stock"
        found["stock" if m.re is _PAIR else "size"] += 1
        rest = _cut(rest, m)
    for pattern, ok in ((_TOTAL, lambda n, m: int(m[1]) == n), (_SOLD_OUT, lambda n, m: n == 0),
                        (_IN_STOCK, lambda n, m: n > 0)):  # fmt: skip
        while m := pattern.search(rest):
            if not now.approved:
                return "not_approved"
            if now.stock is None:
                return "stock_unknown"
            if not ok(now.total, m):
                return "wrong_stock"
            found["stock"] += 1
            rest = _cut(rest, m)
    if any(c.isdigit() for c in rest):
        return "unexplained_number"
    if set(re.findall(r"\w+", rest)) & _SPELLED:
        return "spelled_number"
    if any(found.values()) and name and name not in t:
        return "name_missing"
    need = {"price": "money", "stock": "stock"}.get(answer.kind) if answer.action == "answer" else None
    if answer.action == "ask":
        need = "size"
    if need and not found[need]:
        return "fact_missing"
    return None
