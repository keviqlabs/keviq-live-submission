"""A local replay controller. Its caller supplies trusted snapshots and microphone signals."""

import asyncio
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

DISCLOSURE = "Tôi là trợ lý AI hỗ trợ giới thiệu sản phẩm."
PLAYBACK_TIMEOUT_S = 12.0
_OUTPUT_LEASE = threading.Lock()


class Player(Protocol):
    async def speak(self, text: str) -> None:
        """Return only when output ends; cancellation must stop and drain owned output."""
        ...


def _label(value: str, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value == value.strip()
        and re.fullmatch(r"[\w -]+", value, flags=re.UNICODE) is not None
    )


@dataclass(frozen=True)
class CardSnapshot:
    """An explicit replay approval, not an authentication mechanism for live events.

    Sizes are an approved availability list, never inferred from a catalog's size options.
    None means availability is unknown and must not be spoken.
    """

    card_id: str
    version: int
    name: str
    price_vnd: int
    approved: bool
    available_sizes: tuple[str, ...] | None = None

    def __post_init__(self):
        if not _label(self.card_id, 64) or not _label(self.name, 48):
            raise ValueError("invalid card label")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("invalid card version")
        if type(self.price_vnd) is not int or not 0 < self.price_vnd <= 1_000_000_000:
            raise ValueError("invalid approved price")
        if type(self.approved) is not bool:
            raise ValueError("approval must be explicit")
        sizes = self.available_sizes
        if sizes is not None and (
            type(sizes) is not tuple or len(sizes) > 6 or any(not _label(s, 8) for s in sizes)
        ):
            raise ValueError("invalid approved size availability")

    def sentence(self) -> str:
        if not self.approved:
            raise ValueError("card has not been approved")
        text = f"{self.name}, giá {self.price_vnd:,} đồng."
        if self.available_sizes:
            text += f" Size còn: {', '.join(self.available_sizes)}."
        return text


class Cohost:
    """One session, explicit turns, no automatic restart and no queued product speech.

    COHOST_ENABLED=1 is necessary but not sufficient: the operator must provide a ready
    microphone and explicitly enable this session. A lifecycle fault permanently closes it.
    All calls must run on one asyncio event loop. The process-wide lease permits one speaker.
    """

    def __init__(self, player: Player, *, mode: str, clock: Callable[[], float] = time.monotonic):
        if mode not in {"replay", "demo"}:
            raise ValueError("cohost is restricted to replay or demo")
        self._allowed = os.environ.get("COHOST_ENABLED") == "1"
        self._player = player
        self._clock = clock
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._card: CardSnapshot | None = None
        self._card_until = 0.0
        self._sequence = 0
        self._versions: dict[str, CardSnapshot] = {}
        self._spoken: set[tuple[str, int]] = set()
        self._quiet_since: float | None = None
        self._cooldown_until = 0.0
        self._disclosed = False
        self.enabled = False
        self.latched_off = False
        self.mic_ready = False
        self.host_speaking = False
        self.last_stop_ms: float | None = None
        self.error: str | None = None

    async def microphone_ready(self) -> None:
        async with self._lock:
            self.mic_ready = True
            if not self.host_speaking:
                self._quiet_since = self._clock()

    async def enable(self) -> bool:
        async with self._lock:
            if not self._allowed or self.latched_off or not self.mic_ready:
                return False
            self.enabled = True
            return True

    async def disable(self, reason: str = "operator_off") -> None:
        async with self._lock:
            self.enabled = False
            self.latched_off = True
            self._card = None
            self.error = reason
            await self._stop()

    async def device_changed(self) -> None:
        await self._device_fault("device_changed")

    async def microphone_lost(self) -> None:
        await self._device_fault("microphone_lost")

    async def reconnected(self) -> None:
        await self._device_fault("reconnected")

    async def _device_fault(self, reason: str) -> None:
        async with self._lock:
            self.enabled = False
            self.latched_off = True
            self.mic_ready = False
            self._card = None
            self.error = reason
            await self._stop()

    async def host_activity(self, speaking: bool) -> None:
        if type(speaking) is not bool:
            raise ValueError("host activity must be explicit")
        async with self._lock:
            self.host_speaking = speaking
            self._quiet_since = None if speaking else self._clock()
            if speaking:
                await self._stop()

    async def replace_card(self, sequence: int, card: CardSnapshot | None) -> bool:
        """Revoke speech on a newer source update, even if its card is not eligible.

        The source sequence covers switches and revocations, not just card versions.
        Repeated snapshots cannot refresh the five-second freshness window.
        """
        if type(sequence) is not int or sequence < 1:
            raise ValueError("invalid source sequence")
        if card is not None and not isinstance(card, CardSnapshot):
            raise ValueError("expected a card snapshot")
        async with self._lock:
            if sequence <= self._sequence or self.latched_off:
                return False
            self._sequence = sequence
            old = self._versions.get(card.card_id) if card else None
            if card and old and card.version == old.version and card == old and card == self._card:
                return False
            self._card = None
            await self._stop()
            if card is None:
                return False
            if old and (card.version < old.version or (card.version == old.version and card != old)):
                return False
            self._versions[card.card_id] = card
            if not card.approved:
                return False
            self._card = card
            self._card_until = self._clock() + 5.0
            return True

    def _eligible(self, card: CardSnapshot) -> bool:
        now = self._clock()
        return (
            self.enabled
            and not self.latched_off
            and self.mic_ready
            and not self.host_speaking
            and self._quiet_since is not None
            and now - self._quiet_since >= 1.0
            and now < self._card_until
            and self._card == card
        )

    async def request_turn(self) -> bool:
        async with self._lock:
            card = self._card
            if (
                card is None
                or not self._eligible(card)
                or (self._task is not None and not self._task.done())
                or self._clock() < self._cooldown_until
                or (card.card_id, card.version) in self._spoken
            ):
                return False
            if not _OUTPUT_LEASE.acquire(blocking=False):
                return False
            self._task = asyncio.create_task(self._say(card))
            self._task.add_done_callback(self._completed)
            return True

    def _completed(self, task: asyncio.Task) -> None:
        _OUTPUT_LEASE.release()
        if not task.cancelled() and task.exception() is not None:
            self.enabled = False
            self.latched_off = True
            self.error = "playback_failed"

    async def _say(self, card: CardSnapshot) -> None:
        # The watchdog also covers synthesis that never returns. The player owns cancellation cleanup.
        async with asyncio.timeout(PLAYBACK_TIMEOUT_S):
            if not self._eligible(card):
                return
            if not self._disclosed:
                await self._player.speak(DISCLOSURE)
                self._disclosed = True
            if self._eligible(card):
                self._spoken.add((card.card_id, card.version))
                self._cooldown_until = self._clock() + 20.0
                await self._player.speak(card.sentence())

    async def _stop(self) -> None:
        task = self._task
        if task is None or task.done():
            return
        started = self._clock()
        self.last_stop_ms = None
        if not task.cancelling():
            task.cancel()
        done, _ = await asyncio.wait({task}, timeout=1.0)
        if done:
            # Software acknowledgement only; this does not measure silence at the speaker.
            self.last_stop_ms = (self._clock() - started) * 1000
        else:
            self.enabled = False
            self.latched_off = True
            self.error = "playback_did_not_stop"

    async def wait_idle(self) -> None:
        task = self._task
        if task is not None:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise
