"""Seller-approved facts of a card event, and which card is on screen now.

A card is approved when its status is live or edited and its price is a whole number above 0. Stock is known only
when the card has a SKU and variants (the catalog's stock); otherwise it is None and no number may be said. The
item on screen is the card that appeared last: a new card id, or a known card whose frame time changed. An older
or repeated version of a card is ignored. Standard library only.
"""

import re
import unicodedata
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Facts:
    card_id: str
    version: int
    sku: str | None
    name: str
    price: int | None
    approved: bool
    stock: tuple[tuple[str | None, int], ...] | None
    variant_stock: tuple[tuple[str | None, str | None, int], ...] | None = None

    def for_color(self, color: str | None) -> "Facts":
        """Scope counts to one requested colour; unknown colours never use another variant's stock."""
        if color is None:
            return self
        rows = [v for v in self.variant_stock or () if v[0] == normalize_color(color)]
        stock = size_counts(rows) if rows else None
        name = f"{self.name} màu {rows[0][0]}" if rows else self.name
        return replace(self, stock=stock, name=name)

    def stock_of(self, size: str | None) -> int | None:
        return dict(self.stock).get(size) if self.stock is not None else None

    @property
    def total(self) -> int | None:
        return sum(n for _, n in self.stock) if self.stock is not None else None

    @property
    def sized(self) -> bool:
        return self.stock is not None and any(s is not None for s, _ in self.stock)


def normalize_color(color: str | None) -> str | None:
    return " ".join(unicodedata.normalize("NFC", color).lower().split()) if color else None


def size_counts(rows: list | tuple) -> tuple[tuple[str | None, int], ...]:
    sizes: dict[str | None, int] = {}
    for _, size, n in rows:
        sizes[size] = sizes.get(size, 0) + n
    return tuple(sizes.items())


def display_name(name: str) -> str:
    """The name to say: the catalog code in front ("SP01 giày đen") is not spoken."""
    return re.sub(r"^\s*[A-Z]{1,4}\d{1,4}\s+", "", name).strip()


def facts_from_card(card: dict) -> Facts:
    price = card.get("price")
    approved = (
        card.get("status") in ("live", "edited")
        and isinstance(price, int)
        and not isinstance(price, bool)
        and price > 0
    )
    stock, variants = None, None
    if card.get("sku") and card.get("variants"):
        variants = tuple(
            (
                normalize_color(v.get("color")),
                str(v["size"]).upper() if v.get("size") is not None else None,
                int(v["stock"]),
            )
            for v in card["variants"]
        )
        if len({color for color, _, _ in variants}) == 1:
            stock = size_counts(variants)
    return Facts(
        card["card_id"],
        int(card["version"]),
        card.get("sku"),
        card["name"],
        price if approved else None,
        approved,
        stock,
        variants,
    )


class Board:
    """Latest version of every card, and the card on screen."""

    def __init__(self):
        self._cards: dict[str, dict] = {}
        self._current: str | None = None
        self.switched_ms: int | None = None

    def ingest(self, card: dict, ts_ms: int) -> bool:
        """Take a card event; True when it changed the item on screen."""
        cid = card["card_id"]
        old = self._cards.get(cid)
        if old is not None and card["version"] <= old["version"]:
            return False
        self._cards[cid] = card
        frame = (card.get("provenance") or {}).get("frame_ts_ms")
        appeared = old is None or (
            frame is not None and frame != (old.get("provenance") or {}).get("frame_ts_ms")
        )
        if appeared and cid != self._current:
            self._current, self.switched_ms = cid, ts_ms
            return True
        return False

    def current(self) -> Facts | None:
        if self._current is None:
            return None
        card = self._cards[self._current]
        return None if card.get("status") == "retired" else facts_from_card(card)
