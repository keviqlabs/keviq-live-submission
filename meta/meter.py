"""Daily and total spend caps. Spend comes from model_call events in the store; "today" is the VN calendar day
the store recorded the call (ingested_ms), whatever ts_ms the event carries."""

import logging
from collections.abc import Awaitable, Callable

from app.settings import env_float

log = logging.getLogger("meta.meter")


class BudgetExceeded(RuntimeError):
    def __init__(self, reason: str, spent: float, cap: float):
        super().__init__(f"{reason}: spent {spent:.4f} USD, cap {cap} USD")
        self.reason, self.spent, self.cap = reason, spent, cap


class Meter:
    """`spend()` returns (today_usd, total_usd). Checked before every call; over a cap, no call is made."""

    def __init__(
        self,
        spend: Callable[[], Awaitable[tuple[float, float]]],
        daily_cap: float | None = None,
        total_cap: float | None = None,
        warn_at: float | None = None,
    ):
        self.spend = spend
        self.daily_cap = env_float("MODEL_DAILY_CAP_USD", 30) if daily_cap is None else daily_cap
        self.total_cap = env_float("MODEL_TOTAL_CAP_USD", 300) if total_cap is None else total_cap
        self.warn_at = env_float("MODEL_DAILY_WARN_USD", 20) if warn_at is None else warn_at
        self.warned = False

    async def check(self) -> None:
        today, total = await self.spend()
        if today >= self.daily_cap:
            raise BudgetExceeded("daily_cap", today, self.daily_cap)
        if total >= self.total_cap:
            raise BudgetExceeded("total_cap", total, self.total_cap)
        if today >= self.warn_at and not self.warned:
            self.warned = True
            log.warning(
                "model spend today %.2f USD passed the %.0f USD warning line; tell the Founder",
                today,
                self.warn_at,
            )


def memory_spend(ledger: list[float]) -> Callable[[], Awaitable[tuple[float, float]]]:
    """Spend source for tests: one list of costs counted as both today and total."""

    async def spend() -> tuple[float, float]:
        return sum(ledger), sum(ledger)

    return spend


def http_spend(base_url: str) -> Callable[[], Awaitable[tuple[float, float]]]:
    """Spend source for workers: the app's /api/cost (sums model_call events in PostgreSQL)."""
    import httpx

    async def spend() -> tuple[float, float]:
        async with httpx.AsyncClient(base_url=base_url, timeout=5) as client:
            body = (await client.get("/api/cost")).raise_for_status().json()
        return body["today_usd"], body["total_usd"]

    return spend
