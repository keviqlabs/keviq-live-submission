"""Daily, total and per-session spend caps. Spend comes from model_call events in the store; "today" is the VN
calendar day the store recorded the call (ingested_ms), whatever ts_ms the event carries."""

import logging
from collections.abc import Awaitable, Callable

from app.settings import env, env_float

log = logging.getLogger("meta.meter")


class BudgetExceeded(RuntimeError):
    def __init__(self, reason: str, spent: float, cap: float):
        super().__init__(f"{reason}: spent {spent:.4f} USD, cap {cap} USD")
        self.reason, self.spent, self.cap = reason, spent, cap


class Meter:
    """`spend()` returns (today_usd, total_usd). Checked before every call; over a cap, no call is made.

    Per session: `session_spend(session_id)` returns that session's spend, all time. It is read only when a session
    cap or warning is set (MODEL_SESSION_CAP_USD, MODEL_SESSION_WARN_USD; unset or empty means off) and the call
    names its session. At the cap, `BudgetExceeded("session_cap")`; the warning is logged once per session.

    Limit: the spend is read before each call and there is no reserve, so the calls already in flight when the cap
    is crossed still finish and can take the session over it. A voice stream is checked once, when it opens, and
    its cost is logged when it ends.
    """

    def __init__(
        self,
        spend: Callable[[], Awaitable[tuple[float, float]]],
        daily_cap: float | None = None,
        total_cap: float | None = None,
        warn_at: float | None = None,
        session_spend: Callable[[str], Awaitable[float]] | None = None,
        session_cap: float | None = None,
        session_warn_at: float | None = None,
    ):
        self.spend = spend
        self.daily_cap = env_float("MODEL_DAILY_CAP_USD", 30) if daily_cap is None else daily_cap
        self.total_cap = env_float("MODEL_TOTAL_CAP_USD", 300) if total_cap is None else total_cap
        self.warn_at = env_float("MODEL_DAILY_WARN_USD", 20) if warn_at is None else warn_at
        self.warned = False
        self.session_spend = session_spend
        self.session_cap = _env_usd("MODEL_SESSION_CAP_USD") if session_cap is None else session_cap
        self.session_warn_at = (
            _env_usd("MODEL_SESSION_WARN_USD") if session_warn_at is None else session_warn_at
        )
        self.session_warned: set[str] = set()

    async def check(self, session_id: str | None = None) -> None:
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
        if session_id is None or self.session_spend is None:
            return
        if self.session_cap is None and self.session_warn_at is None:
            return
        spent = await self.session_spend(session_id)
        if self.session_cap is not None and spent >= self.session_cap:
            raise BudgetExceeded("session_cap", spent, self.session_cap)
        if (
            self.session_warn_at is not None
            and spent >= self.session_warn_at
            and session_id not in self.session_warned
        ):
            self.session_warned.add(session_id)
            log.warning(
                "session %s model spend %.2f USD passed the %.2f USD warning line (cap %s USD)",
                session_id,
                spent,
                self.session_warn_at,
                self.session_cap,
            )


def _env_usd(name: str) -> float | None:
    raw = env(name)
    return None if raw is None or raw.strip() == "" else float(raw)


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


def http_session_spend(base_url: str, transport=None) -> Callable[[str], Awaitable[float]]:
    """Per-session spend source for workers: /api/cost?session=<id> → session_usd (all time). Tests pass the app's
    ASGI transport."""
    import httpx

    async def spend(session_id: str) -> float:
        async with httpx.AsyncClient(base_url=base_url, timeout=5, transport=transport) as client:
            body = (await client.get("/api/cost", params={"session": session_id})).raise_for_status().json()
        return body["session_usd"]

    return spend
