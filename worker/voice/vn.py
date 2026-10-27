"""Vietnamese spoken price, sizes and colours: the rule reader used when Spark is not available.

    parse("Đôi này bốn trăm năm mươi nghìn, còn size ba tám, ba chín, bốn mươi", colors={"trắng"})
    -> Spoken(price=450000, sizes=["38", "39", "40"], colors=[])

Prices need a magnitude word (trăm, nghìn/ngàn/k, triệu/tr/củ) or a number of 1000 or more; a bare "bốn trăm
rưỡi" is read as thousands (450000), as sellers say it. Sizes follow "size"/"cỡ": digit pairs ("ba tám" = 38),
numbers ("bốn mươi" = 40), digits or S/M/L letters, until a word that is not part of the list.
"""

import re
import unicodedata
from dataclasses import dataclass, field

DIGITS = {
    "không": 0,
    "một": 1,
    "mốt": 1,
    "hai": 2,
    "ba": 3,
    "bốn": 4,
    "tư": 4,
    "năm": 5,
    "lăm": 5,
    "nhăm": 5,
    "sáu": 6,
    "bảy": 7,
    "bẩy": 7,
    "tám": 8,
    "chín": 9,
}
UNITS = {"nghìn": 1000, "ngàn": 1000, "k": 1000, "triệu": 1_000_000, "tr": 1_000_000, "củ": 1_000_000}
NUMBER_WORDS = {*DIGITS, *UNITS, "mười", "mươi", "trăm", "lẻ", "linh", "rưỡi"}
SIZE_WORDS = {"size", "sai", "sài", "cỡ", "sz"}
SIZE_FILLER = {"còn", "có", "và", "với", "từ", "đến", "tới", "nha", "nhé", "ạ"}
LETTER_SIZES = {"xs", "s", "m", "l", "xl", "xxl"}
TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+|[,;]")
THOUSANDS = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")


@dataclass
class Spoken:
    price: int | None = None
    sizes: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)


def tokens(text: str) -> list[str]:
    out = []
    for t in TOKEN.findall(unicodedata.normalize("NFC", text.lower())):
        if t[0].isdigit() and not THOUSANDS.match(t) and re.search(r"[.,]", t):
            parts = re.split(r"[.,]", t)  # "38,39" is a list, "450.000" is one number
            for i, part in enumerate(parts):
                out.extend([part] if i == 0 else [",", part])
        else:
            out.append(t)
    return out


def _num(t: str) -> int | None:
    return int(re.sub(r"[.,]", "", t)) if t[0].isdigit() else None


def price_value(run: list[str]) -> int | None:
    """Value of a number phrase, or None when it has no magnitude word and is below 1000."""
    total = group = 0
    pending: int | None = None
    last_unit = 1
    has_magnitude = False
    for k, t in enumerate(run):
        n = _num(t)
        if n is not None:
            pending = n if pending is None else pending * 10 + n
        elif t in DIGITS:
            pending = DIGITS[t]
        elif t == "mười":
            group += 10
            pending, last_unit = None, 10
        elif t == "mươi":
            group += (pending or 1) * 10
            pending, last_unit = None, 10
        elif t == "trăm":
            group += (1 if pending is None else pending) * 100
            pending, last_unit, has_magnitude = None, 100, True
        elif t in UNITS:
            total += (group + (pending or 0) or 1) * UNITS[t]
            group, pending, last_unit, has_magnitude = 0, None, UNITS[t], True
        elif t == "rưỡi":  # half of the last unit: "trăm rưỡi" +50, "triệu rưỡi" +500 000
            if last_unit >= 1000:
                total += last_unit // 2
            elif last_unit == 100:
                group += 50
            if k + 1 < len(run) and run[k + 1] not in UNITS:
                break  # "rưỡi" closes the price: "bốn trăm rưỡi một đôi" is 450 000, not 451 000
    if pending is not None:
        # "một triệu hai" = 1 200 000, "năm nghìn hai" = 5 200: a bare digit after a unit is its next place
        group += pending * (last_unit // 10) if last_unit >= 1000 and pending < 10 else pending
    value = total + group
    if not has_magnitude and value < 1000:
        return None
    return (
        value * 1000 if value < 10_000 and not total else value
    )  # "bốn trăm rưỡi" means thousands; "5 nghìn" does not


def size_values(run: list[str]) -> list[str]:
    out, k = [], 0
    while k < len(run):
        t, nxt = run[k], run[k + 1] if k + 1 < len(run) else None
        n = _num(t)
        if n is not None:
            out.append(str(n))
            k += 1
        elif t in DIGITS and nxt == "mươi":
            v, k = DIGITS[t] * 10, k + 2
            if k < len(run) and run[k] in DIGITS and (k + 1 == len(run) or run[k + 1] != "mươi"):
                v, k = v + DIGITS[run[k]], k + 1
            out.append(str(v))
        elif t == "mười":
            v, k = 10, k + 1
            if k < len(run) and run[k] in DIGITS:
                v, k = v + DIGITS[run[k]], k + 1
            out.append(str(v))
        elif t in DIGITS and nxt in DIGITS:
            out.append(f"{DIGITS[t]}{DIGITS[nxt]}")
            k += 2
        elif t in DIGITS:
            out.append(str(DIGITS[t]))
            k += 1
        else:
            k += 1
    return out


def parse(text: str, colors: set[str] | frozenset[str] = frozenset()) -> Spoken:
    toks = tokens(text)
    spoken, in_sizes, i = Spoken(), False, 0
    while i < len(toks):
        t = toks[i]
        if t in SIZE_WORDS:
            in_sizes, i = True, i + 1
            continue
        if t in NUMBER_WORDS or t[0].isdigit():
            j = i
            while j < len(toks) and (toks[j] in NUMBER_WORDS or toks[j][0].isdigit()):
                j += 1
            run = toks[i:j]
            if in_sizes and not any(x in UNITS for x in run):
                spoken.sizes.extend(s for s in size_values(run) if s not in spoken.sizes)
            else:
                in_sizes = False
                value = price_value(run)
                if value is not None and 10_000 <= value <= 100_000_000 and spoken.price is None:
                    spoken.price = value
            i = j
            continue
        if in_sizes and t in LETTER_SIZES:
            if t.upper() not in spoken.sizes:
                spoken.sizes.append(t.upper())
        elif t in colors:
            if t not in spoken.colors:
                spoken.colors.append(t)
        elif t not in {",", ";"} and t not in SIZE_FILLER:
            in_sizes = False
        i += 1
    return spoken
