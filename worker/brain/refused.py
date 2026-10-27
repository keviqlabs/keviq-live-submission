"""A card version the server refused with 409 (D-024): read the card's highest version and see what is left to apply.

The seller's price route can take the number the worker was about to post. The worker then applies its own change on
top of the highest version once, or drops it; it never reuses the refused number (`card.post_version`).
"""

from worker.runner import parse_sse


async def latest_card(ctx, session_id: str, card_id: str) -> dict | None:
    """The card's highest version, read back from the session's events."""
    best = None
    path = f"/api/sessions/{session_id}/events"
    async with ctx.runner.http.stream("GET", path, params={"cursor": 0, "follow": "false"}) as resp:
        resp.raise_for_status()
        async for _, e in parse_sse(resp.aiter_lines()):
            if (
                e["type"] == "card"
                and e["card_id"] == card_id
                and (not best or e["version"] > best["version"])
            ):
                best = e
    return best


def on_top(card: dict, changes: dict) -> dict:
    """What is left of a refused version's changes on the card's highest version: a price only on a card without
    one (D-023), a status only on a draft (live / retired), an image only on a card without one."""
    if card["status"] == "retired":
        return {}
    out = {
        k: v
        for k, v in changes.items()
        if k != "provenance"
        and card[k] != v
        and not (k == "price" and card["price"] is not None)
        and not (k == "status" and card["status"] != "draft")
        and not (k == "image_url" and card["image_url"])
    }
    if out and "provenance" in changes:
        have = card["provenance"]["speech_event_ids"]
        ids = [*have, *[i for i in changes["provenance"]["speech_event_ids"] if i not in have]]
        out["provenance"] = {**card["provenance"], "speech_event_ids": ids}
    return out
