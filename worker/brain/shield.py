"""Scam shield: find a phone number or a link in comment text on receipt, before any model call (C5)."""

import re
import unicodedata

# Kept for existing numeric-only fixture/scoring consumers; scan validates full candidates below.
PHONE = re.compile(r"(?<!\d)(?:\+?84|0)(?:[\s.\-]?\d){9}(?!\d)")
WORDS = dict(zip("không một hai ba bốn năm sáu bảy tám chín".split(), "0123456789", strict=True))
TOKENS = re.compile(r"\w+|[^\w]")
LINK = re.compile(
    r"https?://\S+|www\.\S+"
    r"|\b[\w-]+\.(?:com|net|org|vn|xyz|info|top|link|example|io|me|ly|shop|site|online)\b(?:/\S*)?",
    re.IGNORECASE,
)


def has_phone(text: str) -> bool:
    """Validate maximal digit-word runs; identifiers and ordinary words cannot supply phone fragments."""
    text = unicodedata.normalize("NFC", text).casefold()
    digits, overflow, plus = "", False, False

    def finish() -> bool:
        nonlocal digits, overflow, plus
        valid = not overflow and (
            (not plus and len(digits) == 10 and digits.startswith("0"))
            or (len(digits) == 11 and digits.startswith("84"))
        )
        digits, overflow, plus = "", False, False
        return valid

    for token in TOKENS.finditer(text):
        start, end = token.span()
        first = text[start]
        if first.isalnum() or first == "_":
            # Inspect spans without copying a potentially unbounded digit chunk or identifier.
            numeric = all("0" <= text[i] <= "9" for i in range(start, end))
            word = WORDS.get(text[start:end]) if end - start <= 5 else None
            if numeric or word is not None:
                size = end - start if numeric else 1
                room = 11 - len(digits)
                if size > room:
                    overflow = True
                digits += text[start : start + min(size, room)] if numeric else (word if room else "")
            elif finish():
                return True
        elif first.isspace() or first in ".,-":
            continue
        else:
            if finish():
                return True
            plus = first == "+"
    return finish()


def scan(text: str) -> list[str]:
    """Contact signals in the text: `phone_number`, `link` (sorted, possibly empty)."""
    return (["phone_number"] if has_phone(text) else []) + (["link"] if LINK.search(text) else [])


def first_label(signals: list[str]) -> str:
    """Interim label until the batch answers: a link reads as a scam; a bare number may be a buyer's own."""
    return "scam" if "link" in signals else "other"
