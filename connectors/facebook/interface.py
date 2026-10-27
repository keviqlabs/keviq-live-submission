"""Connector interface shared by the simulator and the real Facebook connector (after the 30/10 gate).

The real connector is not implemented in P0: no real Page, no real Messenger sends, no hiding real comments.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class Messenger(ABC):
    @abstractmethod
    async def send(self, thread_ref: str, template: str, payload: dict) -> dict:
        """Send one templated message; returns the message_out fields. Callers check orders.rules.can_message."""


class CommentSource(ABC):
    @abstractmethod
    def comments(self) -> AsyncIterator[dict]:
        """Yield comment fields (comment_id, user_ref, text, reply_to) with ts_ms."""
