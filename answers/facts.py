"""Seller-approved facts of a card event, and which card is on screen now.

A card is approved when its status is live or edited and its price is a whole number above 0. Stock is known only
when the card has a SKU and variants (the catalog's stock); otherwise it is None and no number may be said. The
item on screen is the card that appeared last: a new card id, or a known card whose frame time changed. An older
or repeated version of a card is ignored. Standard library only.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Facts:
    card_id: str
    version: int
    sku: str | None
    name: str
    price: int | None
    approved: bool
    stock: tuple[tuple[str | None, int], ...] | None

    def stock_of(self, size: str | None) -> int | None:
        return dict(self.stock).get(size) if self.stock is not None else None

    @property
    def total(self) -> int | None:
        return sum(n for _, n in self.stock) if self.stock is not None else None

    @property
    def sized(self) -> bool:
        return self.stock is not None and any(s is not None for s, _ in self.stock)


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
    stock = None
    if card.get("sku") and card.get("variants"):
        sizes: dict[str | None, int] = {}
        for v in card["variants"]:
            size = v.get("size")
            size = str(size).upper() if size is not None else None
            sizes[size] = sizes.get(size, 0) + int(v["stock"])
        stock = tuple(sizes.items())
    return Facts(
        card["card_id"],
        int(card["version"]),
        card.get("sku"),
        card["name"],
        price if approved else None,
        approved,
        stock,
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
