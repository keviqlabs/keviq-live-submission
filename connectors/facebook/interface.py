"""Connector interface shared by the simulator and the real Facebook connector.

The real connector implements CommentSource only (connectors/facebook/comments.py, read-only Graph API):
no real Messenger sends, no hiding real comments.
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


class CommentModerator(ABC):
    @abstractmethod
    async def hide(self, comment_id: str, reasons: list[str]) -> dict:
        """Hide one comment from other viewers (the seller can unhide it). Callers check orders.rules.authorize."""
