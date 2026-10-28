"""Current and previous item identity from frame-time appearances, independent of card edits."""


def appearances(versions: list[dict]) -> list[dict]:
    by_card: dict[str, dict[int, dict]] = {}
    for card in versions:
        by_card.setdefault(card["card_id"], {})[card["version"]] = card
    history = []
    for cards in by_card.values():
        previous = None
        for version in sorted(cards):
            card = cards[version]
            frame = card["provenance"].get("frame_ts_ms")
            if frame is not None and frame != previous:
                history.append(card)
            previous = frame
    return sorted(history, key=lambda c: (c["provenance"]["frame_ts_ms"], c["version"]))


def appearance_ids(versions: list[dict], ts_ms: int) -> list[str]:
    ids = []
    for card in reversed(appearances(versions)):
        if card["provenance"]["frame_ts_ms"] <= ts_ms and card["card_id"] not in ids:
            ids.append(card["card_id"])
    return ids


def current_at(versions: list[dict], ts_ms: int) -> tuple[dict | None, dict | None]:
    """Identity at media time t, carrying each chosen card's latest version from the supplied history."""
    latest = {}
    for card in versions:
        if card["version"] > latest.get(card["card_id"], {}).get("version", 0):
            latest[card["card_id"]] = card
    ids = appearance_ids(versions, ts_ms)
    return tuple(latest[ids[i]] if i < len(ids) else None for i in range(2))
