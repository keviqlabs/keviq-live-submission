"""Which card variant the customer means, and the ask_variant question when the comment did not say enough.

A comment like "lấy đôi này" on a card with sizes 38/39/40 matches several variants. The order flow asks the
customer to choose the part that is missing (size, color, or both) instead of guessing it. The answer is a
`PICK:<order_id>#<i>` quick reply (i indexes the variants of the card version the comment was priced from) or
free text naming the missing part ("39", "size M", "màu đen"). Nothing here reads a model, another customer's
order or "như bạn trên": only the card and the customer's own words.
"""

import re
import unicodedata

from orders.intent import fold

WHAT = {"size": "size", "color": "màu"}
MAX_OPTIONS = 12  # quick replies per question (Meta allows 13)
TITLE_MAX = 20  # Send API quick reply title limit


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


def named(text: str, card: dict, hits: list[int], missing: list[str]) -> list[int]:
    """Variants among `hits` whose every missing part is named in the text."""
    variants = card.get("variants") or []
    return [i for i in hits if missing and all(says(text, variants[i].get(f)) for f in missing)]


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
