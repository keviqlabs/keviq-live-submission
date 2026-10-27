"""Which card variant the customer means, and the ask_variant question when the comment did not say enough.

A comment like "lấy đôi này" on a card with sizes 38/39/40 matches several variants. The order flow asks the
customer to choose the part that is missing (size, color, or both) instead of guessing it. The answer is a
`PICK:<order_id>#<i>` quick reply (i indexes the variants of the card version the comment was priced from) or
free text naming the missing part ("39", "size M", "màu đen"). Nothing here reads a model, another customer's
order or "như bạn trên": only the card and the customer's own words.

Free text picks only when it is plain: the value follows its word ("size 39", "cỡ 40", "màu đen") or the reply
is little more than the value ("Lấy 40 nha", "38"). A refusal ("không lấy 40"), an address ("giao 40 Lê Lợi"),
a question ("39 còn không") or two listed values ("size 39, giao 40 Lê Lợi") is asked again.
"""

import re
import unicodedata

from orders.intent import fold

WHAT = {"size": "size", "color": "màu"}
MAX_OPTIONS = 12  # quick replies per question (Meta allows 13)
TITLE_MAX = 20  # Send API quick reply title limit

# Read on the folded text (no diacritics), so "ko lay 39" and "không lấy 39" are the same refusal.
REFUSAL = re.compile(
    r"\b(?:khong|ko|k|hong|hok|khg|dung|chua|chang|khoi)\s+(?:(?:can|muon|phai|co)\s+)?"
    r"(?:lay|mua|dat|chon|chot|size|sz|co|mau|\d)"
)
FIELD_WORDS = {
    "size": ("size", "sz", "size so"),
    "color": ("mau",),
}  # folded; "cỡ" is read with its diacritics
# Folded words that may stand around the value in a plain answer. Not "co" (có: "có 39?" is a question) and not
# "so" (số: an address).
FILLER = set(
    "lay mua dat chon chot cho minh em e anh chi shop sh oi nha nhe nghen a da vang ok oke okie "
    "doi cai chiec size sz mau di luon thi voi 1 mot".split()
)


def want_of(draft_in: dict) -> dict:
    v = draft_in.get("variant") or {}
    return {"size": v.get("size"), "color": v.get("color")}


def same(a, b) -> bool:
    return (fold(a) if isinstance(a, str) else a) == (fold(b) if isinstance(b, str) else b)


def variant_of(v: dict) -> dict:
    return {"size": v.get("size"), "color": v.get("color")}


def hit_indexes(card: dict, want: dict) -> list[int]:
    """Indexes of the card's variants that agree with every part the customer did say."""
    return [
        i
        for i, v in enumerate(card.get("variants") or [])
        if (want["size"] is None or same(v.get("size"), want["size"]))
        and (want["color"] is None or same(v.get("color"), want["color"]))
    ]


def missing_fields(card: dict, hits: list[int], want: dict) -> list[str]:
    """The parts the customer did not say and that differ between the matching variants."""
    variants = card.get("variants") or []
    return [
        f
        for f in ("size", "color")
        if want[f] is None and len({fold(str(variants[i].get(f) or "")) for i in hits}) > 1
    ]


def choice_text(v: dict, missing: list[str]) -> str:
    return ", ".join(f"size {v.get('size')}" if f == "size" else str(v.get("color")) for f in missing)


def title(v: dict, missing: list[str]) -> str:
    t = choice_text(v, missing)
    return (t[:1].upper() + t[1:])[:TITLE_MAX]


def words(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "").lower()
    return " " + " ".join(re.sub(r"[^\w\s]", " ", t).split()) + " "


def says(text: str, value) -> bool:
    """`value` is a whole word (or words) of `text`. Without diacritics in the text, "den" reads as "đen"; with
    them, only the exact word counts, so "đến" is not "đen"."""
    if value is None or str(value).strip() == "":
        return False
    exact, plain = words(text), " " + fold(text or "") + " "
    if f" {words(str(value)).strip()} " in exact:
        return True
    return exact == plain and f" {fold(str(value))} " in plain


def refuses(text: str) -> bool:
    """A negation before lấy/mua/size/cỡ/màu or a number: "không lấy size 40", "ko lấy 39", "đừng 40"."""
    return bool(REFUSAL.search(fold(text or "")))


def after_field_word(text: str, value, field: str) -> bool:
    plain, v = " " + fold(text or "") + " ", fold(str(value))
    if any(f" {w} {v} " in plain for w in FIELD_WORDS[field]):
        return True
    return field == "size" and f" cỡ {words(str(value)).strip()} " in words(text)


def bare(text: str, values: list) -> bool:
    """The reply is little more than one of `values`: "Lấy 40 nha", "38", "mình lấy đôi 39 nha shop"."""
    plain = " " + fold(text or "") + " "
    for v in sorted({fold(str(v)) for v in values if v is not None}, key=len, reverse=True):
        plain = plain.replace(f" {v} ", " ")
    return all(w in FILLER for w in plain.split())


def named(text: str, card: dict, hits: list[int], missing: list[str]) -> list[int]:
    """Variants among `hits` whose every missing part the text plainly chooses: one listed value per missing part,
    said right after its word or in a reply that is little more than the value, and no refusal."""
    variants = card.get("variants") or []
    if not missing or refuses(text):
        return []
    short = bare(text, [variants[i].get(f) for i in hits for f in missing])
    chosen = {}
    for f in missing:
        said = {
            fold(str(variants[i].get(f))): variants[i].get(f) for i in hits if says(text, variants[i].get(f))
        }
        if len(said) != 1:
            return []  # none, or two listed values ("38 hay 39", "size 39, giao 40 Lê Lợi")
        [value] = said.values()
        if not (short or after_field_word(text, value, f)):
            return []  # "giao 40 Lê Lợi", "mình ở số 39 Trần Phú", "39 còn không"
        chosen[f] = value
    return [i for i in hits if all(same(variants[i].get(f), chosen[f]) for f in missing)]


def picked(payload: dict, hits: list[int]) -> int | None:
    """The variant index of a PICK:<order_id>#<i> quick reply, if it is one of `hits`."""
    qr = payload.get("quick_reply") or ""
    _, sep, n = qr.rpartition("#")
    if qr.startswith("PICK:") and sep and n.isdigit() and int(n) in hits:
        return int(n)
    return None


def question(
    card: dict,
    order_id: str,
    comment_id: str,
    free: list[int],
    missing: list[str],
    *,
    again: bool,
    price: str,
) -> dict:
    """ask_variant payload listing the variants still available (`free`) by their missing part."""
    variants = card["variants"]
    what = " và ".join(WHAT[f] for f in missing)
    listed = "; ".join(choice_text(variants[i], missing) for i in free)
    name = card.get("name") or card.get("sku") or card["card_id"]
    if again:
        text = (
            f"Shop chưa rõ bạn chọn {what} nào cho {name}. Shop còn: {listed}. "
            "Bạn bấm một lựa chọn bên dưới giúp shop nhé."
        )
    else:
        text = f"Bạn chọn {what} nào cho {name} ({price})? Shop còn: {listed}."
    return {
        "order_id": order_id,
        "source_comment_id": comment_id,
        "card_id": card["card_id"],
        "card_version": card["version"],
        "missing": missing,
        "options": [variant_of(variants[i]) for i in free],
        "text": text,
        "quick_replies": [[title(variants[i], missing), f"PICK:{order_id}#{i}"] for i in free[:MAX_OPTIONS]],
    }
