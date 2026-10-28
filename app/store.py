"""Event store: append-only log plus read tables (cards, comments, orders).

Two implementations with one interface: MemoryStore (tests, no database) and PgStore (PostgreSQL).
Every append is idempotent on event_id and projects into the read tables in the same transaction.
A card version that the card already has under another event_id is refused (VersionTaken) and nothing is stored.
"""

import asyncio
import contextlib
import json
import logging

from app.attachment_store import AttachmentDeadlinePassed, AttachmentStorage
from app.contracts import now_ms
from app.current import appearance_ids

log = logging.getLogger("store")

VN_OFFSET_MS = 7 * 3600 * 1000  # Asia/Ho_Chi_Minh, no DST
DAY_MS = 24 * 3600 * 1000
LIVE_STATUSES = ("live", "edited")
# Sessions written by pytest and the smoke run (D-016). Their spend is shown apart but still counts toward
# the caps: a model_call is money already billed, whichever session caused it.
TEST_SESSION_PREFIXES = ("pytest", "smoke-")


class VersionTaken(Exception):
    """The card already has this version under another event_id (D-024)."""


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
    """Latest live/edited versions known at t, ordered by their last frame-time appearance."""
    latest: dict[str, dict] = {}
    known = []
    for card in sorted(versions, key=lambda c: (c["ts_ms"], c["version"])):
        if card["ts_ms"] > ts_ms:
            continue
        known.append(card)
        latest[card["card_id"]] = card
    live = [c for c in latest.values() if c["status"] in LIVE_STATUSES]
    rank = {cid: i for i, cid in enumerate(appearance_ids(known, ts_ms))}
    return sorted(live, key=lambda c: rank.get(c["card_id"], len(rank)))


class MemoryStore(AttachmentStorage):
    kind = "memory"

    def __init__(self) -> None:
        self._init_attachment_storage()
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
        async with self.session_transaction(event["session_id"]):
            prepared_origin = None
            if event["type"] == "comment_attachment" and event["action"] == "ready":
                if self.event_origin() is not None:
                    raise ValueError("source writer cannot create an attachment")
                binding = await self.attachment_binding(event["session_id"])
                if binding is not None:
                    prepared_origin = self.origin_body(_strip_meta(event), binding, self.event_origin())
                if now_ms() >= event["deadline_ms"]:
                    raise AttachmentDeadlinePassed()
            result = self._append(event)
            if result[1]:
                if prepared_origin is not None:
                    await self.record_source_receipt(event["event_id"], prepared_origin)
                else:
                    await self.record_event_origin(_strip_meta(event))
            return result

    def _append(self, event: dict) -> tuple[int, bool]:
        body = _strip_meta(event)
        if body["event_id"] in self._by_id:
            if (
                body["session_id"] in self._binding_rows
                and _strip_meta(self._events[self._by_id[body["event_id"]] - 1]) != body
            ):
                raise VersionTaken("event ID already has another canonical payload")
            return self._by_id[body["event_id"]], False
        if body["type"] == "card" and any(
            c["card_id"] == body["card_id"] and c["version"] == body["version"]
            for c in self._cards.get(body["session_id"], [])
        ):
            raise VersionTaken(f"{body['card_id']} v{body['version']}")
        if body["type"] == "comment_attachment" and any(
            c["type"] == "comment_attachment"
            and c["session_id"] == body["session_id"]
            and c["attachment_id"] == body["attachment_id"]
            and c["version"] == body["version"]
            for c in self._events
        ):
            raise VersionTaken("attachment logical version already exists")
        seq = len(self._events) + 1
        self._events.append(_with_meta(body, seq, now_ms()))
        self._by_id[body["event_id"]] = seq
        self._project(body)
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
        elif e["type"] == "comment_attachment":
            old = self._attachments.setdefault(sid, {}).get(e["attachment_id"])
            if old is None or old["version"] < e["version"]:
                self._attachments[sid][e["attachment_id"]] = e

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

    async def cost_usd(
        self,
        start_ms: int | None = None,
        end_ms: int | None = None,
        test_only: bool = False,
        session_id: str | None = None,
    ) -> float:
        return sum(
            e.get("cost_usd", 0)
            for e in self._events
            if e["type"] == "model_call"
            and (start_ms is None or e["ingested_ms"] >= start_ms)
            and (end_ms is None or e["ingested_ms"] < end_ms)
            and (not test_only or e["session_id"].startswith(TEST_SESSION_PREFIXES))
            and (session_id is None or e["session_id"] == session_id)
        )

    async def costs(self, start_ms: int, end_ms: int, session_id: str | None = None) -> dict[str, float]:
        """today / total / test_today / test_total / session, as cost_usd counts them, in one pass."""
        out = dict.fromkeys(("today", "total", "test_today", "test_total", "session"), 0.0)
        for e in self._events:
            if e["type"] != "model_call":
                continue
            today = start_ms <= e["ingested_ms"] < end_ms
            test = e["session_id"].startswith(TEST_SESSION_PREFIXES)
            hits = {"today": today, "total": True, "test_today": test and today, "test_total": test,
                    "session": e["session_id"] == session_id}  # fmt: skip
            for k, hit in hits.items():
                out[k] += e.get("cost_usd", 0) if hit else 0
        return out


class PgStore(AttachmentStorage):
    kind = "pg"

    def __init__(self, url: str) -> None:
        self._init_attachment_storage()
        self.url = url
        self.notifier = Notifier()
        self._conn = None
        self._lock = asyncio.Lock()
        self._write_conn = None
        self._write_lock = asyncio.Lock()
        self._listener: asyncio.Task | None = None

    async def open(self) -> None:
        self._conn = await self._connect()
        try:
            self._write_conn = await self._connect()
        except BaseException:
            await self._conn.close()
            raise
        self._listener = asyncio.create_task(self._listen())

    async def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return await psycopg.AsyncConnection.connect(self.url, autocommit=True, row_factory=dict_row)

    @contextlib.asynccontextmanager
    async def _session(self):
        """The shared connection under the lock, replaced first if a cancelled call left it busy.

        A cancel through an anyio cancel scope (Starlette's StreamingResponse when an SSE client disconnects)
        that lands mid-query can leave a command or transaction on the connection, and every later query then
        fails with "another command is already in progress". Closing that connection ends its transaction on
        the server without a commit."""
        active = self._active_attachment_connection()
        if active is not None:
            yield active
            return
        from psycopg.pq import TransactionStatus

        async with self._lock:
            status = self._conn.info.transaction_status
            if self._conn.closed or status != TransactionStatus.IDLE:
                log.warning("store connection left %s by a cancelled call; reconnecting", status.name)
                if status == TransactionStatus.ACTIVE:
                    await self._conn.cancel_safe(timeout=2.0)
                await self._conn.close()
                self._conn = await self._connect()
            yield self._conn

    @contextlib.asynccontextmanager
    async def _writer_session(self):
        """A reader can observe committed history while another task holds an open write transaction."""
        from psycopg.pq import TransactionStatus

        async with self._write_lock:
            status = self._write_conn.info.transaction_status
            if self._write_conn.closed or status != TransactionStatus.IDLE:
                if status == TransactionStatus.ACTIVE:
                    await self._write_conn.cancel_safe(timeout=2.0)
                await self._write_conn.close()
                self._write_conn = await self._connect()
            yield self._write_conn

    async def close(self) -> None:
        if self._listener:
            self._listener.cancel()
            await asyncio.gather(self._listener, return_exceptions=True)
        if self._write_conn:
            await self._write_conn.close()
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
        async with self._session() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchall()

    async def append(self, event: dict) -> tuple[int, bool]:
        import psycopg

        try:
            async with self.session_transaction(event["session_id"]):
                return await self._append(event)
        except psycopg.errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "events_attachment_version":
                raise VersionTaken("attachment logical version already exists") from exc
            raise

    async def _append(self, event: dict) -> tuple[int, bool]:
        body = _strip_meta(event)
        async with self._session() as conn:
            cache, scope = (
                self._attachment_cache.get(),
                "source" if self.event_origin() is not None else "server",
            )
            if cache.get(("write_scope",)) != scope:
                await conn.execute("SELECT set_config('keviq.attachment_write_scope', %s, true)", (scope,))
                cache[("write_scope",)] = scope
            if body["type"] == "comment_attachment":
                binding = await self.attachment_binding(body["session_id"])
                if binding is not None:
                    return await self._append_attachment(body, binding)
            written_ms = now_ms()
            values = (
                body["event_id"],
                body["session_id"],
                body["type"],
                body["ts_ms"],
                written_ms,
                json.dumps(body),
            )
            origin = self.event_origin()
            receipt = None
            if origin is not None:
                binding = await self.attachment_binding(body["session_id"])
                if binding is None or body["type"] in {"comment_attachment", "order", "message_out"}:
                    raise ValueError("source writer requires an admitted non-decision event")
                receipt = self.origin_body(body, binding, origin)
                projection = (
                    ", projected AS (INSERT INTO cards(session_id,card_id,version,ts_ms,status,sku,price,event_id,body) "
                    "SELECT session_id,body->>'card_id',(body->>'version')::integer,(body->>'ts_ms')::bigint,"
                    "body->>'status',body->>'sku',(body->>'price')::bigint,event_id,body FROM inserted "
                    "ON CONFLICT DO NOTHING RETURNING version)"
                    if body["type"] == "card"
                    else ""
                )
                cur = await conn.execute(
                    "WITH inserted AS (INSERT INTO events(event_id,session_id,type,ts_ms,ingested_ms,body) "
                    "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(event_id) DO NOTHING "
                    "RETURNING seq,event_id,session_id,body), "
                    "receipt AS (INSERT INTO attachment_source_receipts(event_id,session_id,body) "
                    "SELECT event_id,session_id,%s::jsonb FROM inserted RETURNING event_id) "
                    + projection
                    + (
                        " SELECT seq,EXISTS(SELECT 1 FROM projected) AS projected FROM inserted"
                        if projection
                        else " SELECT seq FROM inserted"
                    ),
                    (*values, json.dumps(receipt)),
                )
            else:
                cur = await conn.execute(
                    "INSERT INTO events (event_id, session_id, type, ts_ms, ingested_ms, body)"
                    " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING RETURNING seq",
                    values,
                )
            row = await cur.fetchone()
            if row is None:
                cur = await conn.execute(
                    "SELECT seq,body FROM events WHERE event_id = %s", (body["event_id"],)
                )
                canonical = await cur.fetchone()
                if (
                    await self.attachment_binding(body["session_id"]) is not None
                    and canonical["body"] != body
                ):
                    raise VersionTaken("event ID already has another canonical payload")
                return canonical["seq"], False
            if receipt is not None and body["type"] == "card":
                if not row["projected"]:
                    raise VersionTaken(f"{body['card_id']} v{body['version']}")
                if ("cards", body["session_id"]) in cache:
                    cache[("cards", body["session_id"])] = [*cache[("cards", body["session_id"])], body]
            else:
                await self._project(body)
            if receipt is None:
                await self.record_event_origin(body)
            else:
                cache[("origin", body["event_id"])] = receipt
            cache[("written", body["event_id"])] = _with_meta(body, row["seq"], written_ms)
        self.notifier.notify()
        return row["seq"], True

    async def _append_attachment(self, body, binding):
        if self.event_origin() is not None:
            raise ValueError("source writer cannot create an attachment")
        serialized = json.dumps(body)
        origin = json.dumps(self.origin_body(body, binding))
        written_ms = now_ms()
        if body["action"] == "ready" and written_ms >= body["deadline_ms"]:
            raise AttachmentDeadlinePassed()
        cur = await self._active_attachment_connection().execute(
            "WITH inserted AS (INSERT INTO events(event_id,session_id,type,ts_ms,ingested_ms,body) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(event_id) DO NOTHING RETURNING seq,event_id,session_id,body), "
            "projected AS (INSERT INTO attachments(session_id,attachment_id,version,event_id,body,summary) "
            "SELECT session_id,body->>'attachment_id',(body->>'version')::bigint,event_id,body, "
            "body-'readiness_evidence' FROM inserted ON CONFLICT(session_id,attachment_id) DO UPDATE SET "
            "version=EXCLUDED.version,event_id=EXCLUDED.event_id,body=EXCLUDED.body,summary=EXCLUDED.summary "
            "WHERE attachments.version < EXCLUDED.version RETURNING event_id), "
            "receipt AS (INSERT INTO attachment_source_receipts(event_id,session_id,body) "
            "SELECT event_id,session_id,%s::jsonb FROM inserted RETURNING event_id) SELECT seq FROM inserted",
            (
                body["event_id"],
                body["session_id"],
                body["type"],
                body["ts_ms"],
                written_ms,
                serialized,
                origin,
            ),
        )
        row = await cur.fetchone()
        if row is None:
            rows = await self._fetch("SELECT seq,body FROM events WHERE event_id=%s", (body["event_id"],))
            if rows[0]["body"] != body:
                raise VersionTaken("attachment event ID already has another canonical payload")
            return rows[0]["seq"], False
        self.notifier.notify()
        return row["seq"], True

    async def _project(self, e: dict) -> None:
        sid, ex = e["session_id"], self._active_attachment_connection().execute
        if e["type"] == "card":
            cur = await ex(
                "INSERT INTO cards (session_id, card_id, version, ts_ms, status, sku, price, event_id, body)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING version",
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
            if await cur.fetchone() is None:  # raising here rolls back the event insert too
                raise VersionTaken(f"{e['card_id']} v{e['version']}")
            cache = self._attachment_cache.get()
            if ("cards", sid) in cache:
                cache[("cards", sid)] = [*cache[("cards", sid)], e]
        elif e["type"] == "comment_attachment":
            await ex(
                "INSERT INTO attachments(session_id,attachment_id,version,event_id,body,summary) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(session_id,attachment_id) DO UPDATE SET version=EXCLUDED.version,event_id=EXCLUDED.event_id,body=EXCLUDED.body,summary=EXCLUDED.summary "
                "WHERE attachments.version < EXCLUDED.version",
                (
                    sid,
                    e["attachment_id"],
                    e["version"],
                    e["event_id"],
                    json.dumps(e),
                    json.dumps({k: v for k, v in e.items() if k != "readiness_evidence"}),
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
        cache = self._attachment_cache.get() if self._active_attachment_connection() is not None else None
        if cache is not None and ("cards", session_id) in cache:
            return list(cache[("cards", session_id)])
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

    async def cost_usd(
        self,
        start_ms: int | None = None,
        end_ms: int | None = None,
        test_only: bool = False,
        session_id: str | None = None,
    ) -> float:
        # The window is on ingested_ms (when the store recorded the call), not ts_ms: a worker's model_call takes its
        # trigger's ts_ms, which may be any day (ts_ms=1 in a test, an old replay), but the money is spent now.
        prefixes = [p + "%" for p in TEST_SESSION_PREFIXES] if test_only else ["%"]
        one = session_id is not None
        rows = await self._fetch(
            "SELECT COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd FROM events WHERE type = 'model_call'"
            " AND ingested_ms >= %s AND ingested_ms < %s AND session_id LIKE ANY(%s)"
            + (" AND session_id = %s" if one else ""),
            (
                start_ms if start_ms is not None else 0,
                end_ms if end_ms is not None else 2**62,
                prefixes,
                *([session_id] if one else []),
            ),
        )
        return float(rows[0]["usd"])

    async def costs(self, start_ms: int, end_ms: int, session_id: str | None = None) -> dict[str, float]:
        """today / total / test_today / test_total / session, as cost_usd counts them, in one statement: one
        snapshot, so a call recorded meanwhile is in every sum or in none (session is 0 when session_id is None)."""
        rows = await self._fetch(
            "SELECT COALESCE(SUM(usd) FILTER (WHERE today), 0) AS today, COALESCE(SUM(usd), 0) AS total,"
            " COALESCE(SUM(usd) FILTER (WHERE test AND today), 0) AS test_today,"
            " COALESCE(SUM(usd) FILTER (WHERE test), 0) AS test_total,"
            " COALESCE(SUM(usd) FILTER (WHERE session_id = %s), 0) AS session"
            " FROM (SELECT (body->>'cost_usd')::numeric AS usd, session_id, session_id LIKE ANY(%s) AS test,"
            " ingested_ms >= %s AND ingested_ms < %s AS today FROM events WHERE type = 'model_call') AS m",
            (session_id, [p + "%" for p in TEST_SESSION_PREFIXES], start_ms, end_ms),
        )
        return {k: float(v) for k, v in rows[0].items()}


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
        "attachments": await store.attachments(session_id),
        "attachment_enabled": await store.attachment_binding(session_id) is not None,
        "cost": {"today_usd": await store.cost_usd(day_start, day_end), "total_usd": await store.cost_usd()},
    }


def make_store(url: str | None):
    return PgStore(url) if url else MemoryStore()
