"""Which card a final speech line can go to (D-025). Pure functions; `worker/brain/card.py` holds the state.

- Each `is_new` product_seen opens a span on its card's media clock, `[frame_ts_ms - D_MS, the next is_new
  product_seen's frame_ts_ms)`. A card reopened by SKU gets another span.
- The line's candidates are the cards that are not retired and have a span overlapping `[start_ms, end_ms]`.
- A heard price goes to a card only when the line has a magnitude word and exactly one candidate fits every kind
  of item the line names (all candidates fit a line that names none). Otherwise the price waits for the seller.
"""

from worker.voice import vn

# Frames are cut at 1 fps (D-021): an item can be in view up to one frame before the first frame that shows it.
D_MS = 1000
# product_seen latency: ledger p95 of the 351 test1 frame reads at the current prompt and effort, run
# probe-test1-6b-1950 (worker/vision/spark.py). Re-measure on the live path and whenever the effort changes.
H_MS = 3168
# A line is decided at max(read done, received + HOLD_MS), so a product_seen still being read can arrive first.
HOLD_MS = D_MS + H_MS

MAGNITUDE = {"nghìn", "ngàn", "trăm", "triệu", "củ"}
STUCK = {"k", "tr"}  # a magnitude only right after a number: 450k, 1tr2
SHOES = ("sneaker", "boot", "loafer", "heels", "sandal")
TOPS = ("shirt", "tshirt", "sweater", "jacket")
KINDS = {
    ("giày",): SHOES,
    ("đôi",): SHOES,
    ("áo",): TOPS,
    ("tay", "cầm"): ("other",),
    ("bàn", "phím"): ("other",),
    # from the catalogue names; no test1 line has these
    ("túi",): ("bag",),
    ("thắt", "lưng"): ("belt",),
    ("váy",): ("dress", "skirt"),
    ("mũ",): ("hat",),
    ("quần",): ("jeans", "trousers"),
    ("dép",): ("sandal",),
    ("sandal",): ("sandal",),
    ("boot",): ("boot",),
}


def open_span(spans: list[list], frame_ts_ms: int) -> None:
    spans.append([frame_ts_ms - D_MS, None])


def close_span(spans: list[list], frame_ts_ms: int) -> None:
    if spans and spans[-1][1] is None:
        spans[-1][1] = frame_ts_ms


def overlaps(spans: list[list], start_ms: int, end_ms: int) -> bool:
    return any(a <= end_ms and (b is None or start_ms < b) for a, b in spans)


def has_magnitude(toks: list[str]) -> bool:
    return any(t in MAGNITUDE or (t in STUCK and i and toks[i - 1][0].isdigit()) for i, t in enumerate(toks))


def kinds_said(toks: list[str]) -> list[tuple[str, ...]]:
    """The categories each kind word in the line allows."""
    return [
        cats
        for words, cats in KINDS.items()
        if any(tuple(toks[i : i + len(words)]) == words for i in range(len(toks)))
    ]


def choose(text: str, candidates: dict[str, str]) -> tuple[list[str], str | None]:
    """The candidates (card_id -> category) that fit every kind the line names, all of them when it names none,
    and why a heard price waits (None when it can go to the one card left)."""
    toks = vn.tokens(text)
    said = kinds_said(toks)
    keep = [cid for cid, category in candidates.items() if all(category in cats for cats in said)]
    if not has_magnitude(toks):
        return keep, "no_magnitude"
    if len(keep) != 1:
        return keep, "many" if keep else "none"
    return keep, None
