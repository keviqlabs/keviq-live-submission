"""Scam shield: find a phone number or a link in comment text on receipt, before any model call (C5)."""

import re

# Vietnamese numbers: 0 or +84 then 9 digits, spaces/dots/dashes allowed between digits ("0900 000 012").
PHONE = re.compile(r"(?<!\d)(?:\+?84|0)(?:[\s.\-]?\d){9}(?!\d)")
LINK = re.compile(
    r"https?://\S+|www\.\S+"
    r"|\b[\w-]+\.(?:com|net|org|vn|xyz|info|top|link|example|io|me|ly|shop|site|online)\b(?:/\S*)?",
    re.IGNORECASE,
)


def scan(text: str) -> list[str]:
    """Contact signals in the text: `phone_number`, `link` (sorted, possibly empty)."""
    found = [("phone_number", PHONE), ("link", LINK)]
    return [name for name, rx in found if rx.search(text)]


def first_label(signals: list[str]) -> str:
    """Interim label until the batch answers: a link reads as a scam; a bare number may be a buyer's own."""
    return "scam" if "link" in signals else "other"
