"""Event store: append-only log plus read tables (cards, comments, orders).

Two implementations with one interface: MemoryStore (tests, no database) and PgStore (PostgreSQL).
Every append is idempotent on event_id and projects into the read tables in the same transaction.
"""

import asyncio
import json
import logging

from app.contracts import now_ms

log = logging.getLogger("store")

VN_OFFSET_MS = 7 * 3600 * 1000  # Asia/Ho_Chi_Minh, no DST
DAY_MS = 24 * 3600 * 1000
LIVE_STATUSES = ("live", "edited")


def vn_day_bounds(ms: int) -> tuple[int, int]:
    start = (ms + VN_OFFSET_MS) // DAY_MS * DAY_MS - VN_OFFSET_MS
    return start, start + DAY_MS


class Notifier:
    """Wake SSE readers when new events land. Take a token before reading, then wait on it (no lost wakeups)."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def token(self) -> asyncio.Event:
        return self._event

    def notify(self) -> None:
        self._event.set()
        self._event = asyncio.Event()

    @staticmethod
    async def wait(token: asyncio.Event, seconds: float) -> bool:
        try:
            await asyncio.wait_for(token.wait(), seconds)
            return True
        except TimeoutError:
            return False


def _with_meta(body: dict, seq: int, ingested_ms: int) -> dict:
    return {**body, "seq": seq, "ingested_ms": ingested_ms}


def _strip_meta(event: dict) -> dict:
    return {k: v for k, v in event.items() if k not in ("seq", "ingested_ms")}


def live_cards_from(versions: list[dict], ts_ms: int) -> list[dict]:
    """Cards live at ts_ms: latest version with ts_ms <= t per card, status live/edited; newest card first."""
    latest: dict[str, dict] = {}
    first_seen: dict[str, int] = {}
    for card in sorted(versions, key=lambda c: (c["ts_ms"], c["version"])):
        if card["ts_ms"] > ts_ms:
            continue
        first_seen.setdefault(card["card_id"], card["ts_ms"])
        latest[card["card_id"]] = card
    live = [c for c in latest.values() if c["status"] in LIVE_STATUSES]
    return sorted(live, key=lambda c: first_seen[c["card_id"]], reverse=True)


class MemoryStore:
    kind = "memory"

    def __init__(self) -> None:
        self.notifier = Notifier()
        self._events: list[dict] = []
        self._by_id: dict[str, int] = {}
        self._cards: dict[str, list[dict]] = {}
        self._comments: dict[str, dict[str, dict]] = {}
        self._orders: dict[str, dict[str, dict]] = {}

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def append(self, event: dict) -> tuple[int, bool]:
        body = _strip_meta(event)
        if body["event_id"] in self._by_id:
            return self._by_id[body["event_id"]], False
        seq = len(self._events) + 1
        self._events.append(_with_meta(body, seq, now_ms()))
        self._by_id[body["event_id"]] = seq
        self._project(body)
        self.notifier.notify()
        return seq, True

    def _project(self, e: dict) -> None:
        sid = e["session_id"]
        if e["type"] == "card":
            self._cards.setdefault(sid, []).append(e)
        elif e["type"] == "comment":
            self._comments.setdefault(sid, {}).setdefault(e["comment_id"], {"comment": e, "labeled": None})[
                "comment"
            ] = e
        elif e["type"] == "comment_labeled":
            self._comments.setdefault(sid, {}).setdefault(
                e["comment_id"], {"comment": None, "labeled": None}
            )["labeled"] = e
        elif e["type"] == "order":
            self._orders.setdefault(sid, {})[e["order_id"]] = e

    async def read(self, session_id: str | None, after_seq: int = 0, limit: int = 500) -> list[dict]:
        out = []
        for e in self._events[after_seq:]:
            if session_id is None or e["session_id"] == session_id:
                out.append(e)
                if len(out) >= limit:
                    break
        return out

    async def head_seq(self) -> int:
        return len(self._events)

    async def sessions(self) -> list[dict]:
        stats: dict[str, dict] = {}
        for e in self._events:
            s = stats.setdefault(
                e["session_id"], {"session_id": e["session_id"], "events": 0, "last_ts_ms": 0}
            )
            s["events"] += 1
            s["last_ts_ms"] = max(s["last_ts_ms"], e["ts_ms"])
        return sorted(stats.values(), key=lambda s: s["last_ts_ms"], reverse=True)

    async def card_versions(self, session_id: str) -> list[dict]:
        return list(self._cards.get(session_id, []))

    async def comments(self, session_id: str) -> list[dict]:
        rows = self._comments.get(session_id, {}).values()
        return [r for r in rows if r["comment"] is not None]

    async def orders(self, session_id: str) -> list[dict]:
        return list(self._orders.get(session_id, {}).values())

    async def cost_usd(self, start_ms: int | None = None, end_ms: int | None = None) -> float:
        return sum(
            e.get("cost_usd", 0)
            for e in self._events
            if e["type"] == "model_call"
            and (start_ms is None or e["ts_ms"] >= start_ms)
            and (end_ms is None or e["ts_ms"] < end_ms)
        )


class PgStore:
    kind = "pg"

    def __init__(self, url: str) -> None:
        self.url = url
        self.notifier = Notifier()
        self._conn = None
        self._lock = asyncio.Lock()
        self._listener: asyncio.Task | None = None

    async def open(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._conn = await psycopg.AsyncConnection.connect(self.url, autocommit=True, row_factory=dict_row)
        self._listener = asyncio.create_task(self._listen())

    async def close(self) -> None:
        if self._listener:
            self._listener.cancel()
        if self._conn:
            await self._conn.close()

    async def _listen(self) -> None:
        """Wake readers on inserts from any process (worker, replay, another app instance)."""
        import psycopg

        while True:
            try:
                async with await psycopg.AsyncConnection.connect(self.url, autocommit=True) as conn:
                    await conn.execute("LISTEN events")
                    async for _ in conn.notifies():
                        self.notifier.notify()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # reconnect; readers still poll on keepalive timeout
                log.warning("LISTEN connection lost: %s; retrying in 1 s", exc)
                await asyncio.sleep(1)

    async def _fetch(self, sql: str, params: tuple = ()) -> list[dict]:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            return await cur.fetchall()

    async def append(self, event: dict) -> tuple[int, bool]:
        body = _strip_meta(event)
        async with self._lock, self._conn.transaction():
            cur = await self._conn.execute(
                "INSERT INTO events (event_id, session_id, type, ts_ms, ingested_ms, body)"
                " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING RETURNING seq",
                (
                    body["event_id"],
                    body["session_id"],
                    body["type"],
                    body["ts_ms"],
                    now_ms(),
                    json.dumps(body),
                ),
            )
            row = await cur.fetchone()
            if row is None:
                cur = await self._conn.execute(
                    "SELECT seq FROM events WHERE event_id = %s", (body["event_id"],)
                )
                return (await cur.fetchone())["seq"], False
            await self._project(body)
        self.notifier.notify()
        return row["seq"], True

    async def _project(self, e: dict) -> None:
        sid, ex = e["session_id"], self._conn.execute
        if e["type"] == "card":
            await ex(
                "INSERT INTO cards (session_id, card_id, version, ts_ms, status, sku, price, event_id, body)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    sid,
                    e["card_id"],
                    e["version"],
                    e["ts_ms"],
                    e["status"],
                    e["sku"],
                    e["price"],
                    e["event_id"],
                    json.dumps(e),
                ),
            )
        elif e["type"] == "comment":
            await ex(
                "INSERT INTO comments (session_id, comment_id, ts_ms, user_ref, text) VALUES (%s,%s,%s,%s,%s)"
                " ON CONFLICT (session_id, comment_id) DO UPDATE SET ts_ms = EXCLUDED.ts_ms,"
                " user_ref = EXCLUDED.user_ref, text = EXCLUDED.text",
                (sid, e["comment_id"], e["ts_ms"], e["user_ref"], e["text"]),
            )
        elif e["type"] == "comment_labeled":
            await ex(
                "INSERT INTO comments (session_id, comment_id, ts_ms, user_ref, text, label, action, labeled_body)"
                " VALUES (%s,%s,%s,'',%s,%s,%s,%s) ON CONFLICT (session_id, comment_id) DO UPDATE SET"
                " label = EXCLUDED.label, action = EXCLUDED.action, labeled_body = EXCLUDED.labeled_body",
                (sid, e["comment_id"], e["ts_ms"], "", e["label"], e["action"], json.dumps(e)),
            )
        elif e["type"] == "order":
            await ex(
                "INSERT INTO orders (session_id, order_id, state, approval_needed, amount, updated_ts_ms, body)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (session_id, order_id) DO UPDATE SET"
                " state = EXCLUDED.state, approval_needed = EXCLUDED.approval_needed, amount = EXCLUDED.amount,"
                " updated_ts_ms = EXCLUDED.updated_ts_ms, body = EXCLUDED.body",
                (
                    sid,
                    e["order_id"],
                    e["state"],
                    e["approval_needed"],
                    e["amount"],
                    e["ts_ms"],
                    json.dumps(e),
                ),
            )

    async def read(self, session_id: str | None, after_seq: int = 0, limit: int = 500) -> list[dict]:
        if session_id is None:
            rows = await self._fetch(
                "SELECT seq, ingested_ms, body FROM events WHERE seq > %s ORDER BY seq LIMIT %s",
                (after_seq, limit),
            )
        else:
            rows = await self._fetch(
                "SELECT seq, ingested_ms, body FROM events WHERE session_id = %s AND seq > %s ORDER BY seq LIMIT %s",
                (session_id, after_seq, limit),
            )
        return [_with_meta(r["body"], r["seq"], r["ingested_ms"]) for r in rows]

    async def head_seq(self) -> int:
        rows = await self._fetch("SELECT COALESCE(MAX(seq), 0) AS seq FROM events")
        return rows[0]["seq"]

    async def sessions(self) -> list[dict]:
        return await self._fetch(
            "SELECT session_id, COUNT(*) AS events, MAX(ts_ms) AS last_ts_ms FROM events"
            " GROUP BY session_id ORDER BY last_ts_ms DESC LIMIT 100"
        )

    async def card_versions(self, session_id: str) -> list[dict]:
        rows = await self._fetch(
            "SELECT body FROM cards WHERE session_id = %s ORDER BY ts_ms, version", (session_id,)
        )
        return [r["body"] for r in rows]

    async def comments(self, session_id: str) -> list[dict]:
        rows = await self._fetch(
            "SELECT e.body AS comment, c.labeled_body AS labeled FROM comments c"
            " JOIN events e ON e.session_id = c.session_id AND e.type = 'comment' AND e.body->>'comment_id' = c.comment_id"
            " WHERE c.session_id = %s ORDER BY c.ts_ms",
            (session_id,),
        )
        return [dict(r) for r in rows]

    async def orders(self, session_id: str) -> list[dict]:
        rows = await self._fetch(
            "SELECT body FROM orders WHERE session_id = %s ORDER BY updated_ts_ms", (session_id,)
        )
        return [r["body"] for r in rows]

    async def cost_usd(self, start_ms: int | None = None, end_ms: int | None = None) -> float:
        rows = await self._fetch(
            "SELECT COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd FROM events WHERE type = 'model_call'"
            " AND ts_ms >= %s AND ts_ms < %s",
            (start_ms if start_ms is not None else 0, end_ms if end_ms is not None else 2**62),
        )
        return float(rows[0]["usd"])


async def session_state(store, session_id: str, ts_ms: int | None = None) -> dict:
    versions = await store.card_versions(session_id)
    latest: dict[str, dict] = {}
    for c in versions:
        latest[c["card_id"]] = c
    orders = await store.orders(session_id)
    day_start, day_end = vn_day_bounds(now_ms())
    return {
        "session_id": session_id,
        "cards": list(latest.values()),
        "live_cards": live_cards_from(versions, ts_ms if ts_ms is not None else 2**62),
        "comments": await store.comments(session_id),
        "orders": orders,
        "approval_queue": [o for o in orders if o["state"] == "NEEDS_SELLER"],
        "cost": {"today_usd": await store.cost_usd(day_start, day_end), "total_usd": await store.cost_usd()},
    }


def make_store(url: str | None):
    return PgStore(url) if url else MemoryStore()
