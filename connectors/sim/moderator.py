"""Simulated comment moderation: `hide()` only records and logs. No real comment is hidden in P0."""

import logging

from connectors.facebook.interface import CommentModerator

log = logging.getLogger("connectors.sim.moderator")


class SimModerator(CommentModerator):
    def __init__(self):
        self.hidden: list[dict] = []

    async def hide(self, comment_id: str, reasons: list[str]) -> dict:
        out = {"comment_id": comment_id, "hidden": True, "reasons": list(reasons)}
        self.hidden.append(out)
        log.info("sim hide %s (%s)", comment_id, ", ".join(reasons))
        return out
