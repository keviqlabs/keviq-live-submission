"""Server-owned session bindings and transaction boundaries for attachment decisions."""

import asyncio
import copy
import hashlib
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar


class AttachmentDeadlinePassed(Exception):
    """Preparation consumed the remaining initial wait before a ready write could begin."""


class AttachmentStorage:
    def _init_attachment_storage(self):
        self._attachment_tx = ContextVar(f"attachment_transaction_{id(self)}", default=None)
        self._attachment_mutex = asyncio.Lock()
        self._source_context = ContextVar(f"source_context_{id(self)}", default=None)
        self._attachment_cache = ContextVar(f"attachment_cache_{id(self)}", default=None)
        self._binding_rows = {}
        self._binding_history = {}
        self._source_rows = {}
        self._order_intents = {}
        self._attachments = {}
        self._consumed_inputs = set()

    @asynccontextmanager
    async def source_write(self, origin):
        token = self._source_context.set((asyncio.current_task(), origin))
        try:
            yield
        finally:
            self._source_context.reset(token)

    def event_origin(self):
        value = self._source_context.get()
        return value[1] if value and value[0] is asyncio.current_task() else None

    def written_event(self, event_id):
        if self.kind == "memory":
            seq = self._by_id.get(event_id)
            return (self._events[seq - 1], self._source_rows.get(event_id)) if seq else (None, None)
        cache = self._attachment_cache.get()
        return cache.get(("written", event_id)), cache.get(("origin", event_id))

    def prime_card_guard(self, session_id, events):
        if self.kind == "pg" and self._active_attachment_connection() is not None:
            self._attachment_cache.get()[("cards", session_id)] = [
                {k: v for k, v in event.items() if k not in {"seq", "ingested_ms"}}
                for event in events
                if event["type"] == "card"
            ]

    async def record_event_origin(self, event):
        binding = await self.attachment_binding(event["session_id"])
        if binding is None:
            return
        origin = self.event_origin()
        if origin is not None and event["type"] in {"comment_attachment", "order", "message_out"}:
            raise ValueError("source writer cannot author a server decision")
        body = self.origin_body(event, binding, origin)
        await self.record_source_receipt(event["event_id"], body)

    @staticmethod
    def origin_body(event, binding, origin=None):
        digest = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {
            "session_id": event["session_id"],
            "kind": event["type"],
            "source": event["source"],
            "stream_id": binding.get("stream_id", event["session_id"]),
            "digest": digest,
            **(origin or {"principal": "app", "epoch": None, "role": "server"}),
        }

    async def append_order_batch(self, events, *, intent=None, attachment=None):
        """One atomic server continuation; intermediate order states remain in the immutable log."""
        from app.store import now_ms

        if not events:
            return
        sid = events[0]["session_id"]
        if any(e["session_id"] != sid or e["type"] not in {"order", "message_out"} for e in events):
            raise ValueError("batch is only a same-session server order continuation")
        if attachment is not None:
            if (
                attachment["type"] != "comment_attachment"
                or attachment["session_id"] != sid
                or attachment["outcome"] != "expired"
                or attachment["action"] != "expire"
                or attachment["version"] != 1
            ):
                raise ValueError("only an initial expired outcome can join its candidate order")
            events = [attachment, *events]
        async with self.session_transaction(sid):
            if self.event_origin() is not None:
                raise ValueError("source capability cannot create an order continuation")
            if self.kind == "memory":
                if intent is not None:
                    await self.reserve_attachment_order(sid, *intent)
                return {event["event_id"]: (await self.append(event))[0] for event in events}
            conn, cache = self._active_attachment_connection(), self._attachment_cache.get()
            if cache.get(("write_scope",)) != "server":
                await conn.execute("SELECT set_config('keviq.attachment_write_scope','server',true)")
                cache[("write_scope",)] = "server"
            binding = await self.attachment_binding(sid)
            if binding is None:
                raise ValueError("an attachment continuation requires its durable session binding")
            origins = [
                {"event_id": event["event_id"], "body": self.origin_body(event, binding)} for event in events
            ]
            intent_rows = [] if intent is None else [{"comment_id": intent[0], "order_id": intent[1]}]
            cur = await conn.execute(
                "WITH intent_input AS (SELECT x->>'comment_id' AS comment_id,x->>'order_id' AS order_id "
                "FROM jsonb_array_elements(%s::jsonb) AS x), "
                "reserved AS (INSERT INTO attachment_order_intents(session_id,comment_id,order_id) "
                "SELECT %s,comment_id,order_id FROM intent_input ON CONFLICT(session_id,comment_id) "
                "DO NOTHING RETURNING order_id), "
                "input AS (SELECT body,n FROM jsonb_array_elements(%s::jsonb) WITH ORDINALITY AS x(body,n)), "
                "inserted AS (INSERT INTO events(event_id,session_id,type,ts_ms,ingested_ms,body) "
                "SELECT body->>'event_id',body->>'session_id',body->>'type',(body->>'ts_ms')::bigint,%s,body "
                "FROM input ORDER BY n ON CONFLICT(event_id) DO NOTHING RETURNING seq,event_id,session_id,body), "
                "receipts AS (INSERT INTO attachment_source_receipts(event_id,session_id,body) "
                "SELECT i.event_id,i.session_id,r->'body' FROM inserted i "
                "JOIN jsonb_array_elements(%s::jsonb) AS r ON r->>'event_id'=i.event_id RETURNING event_id), "
                "latest AS (SELECT DISTINCT ON (body->>'order_id') body FROM inserted "
                "WHERE body->>'type'='order' ORDER BY body->>'order_id',seq DESC), "
                "attachment_projection AS (INSERT INTO attachments(session_id,attachment_id,version,event_id,body,summary) "
                "SELECT session_id,body->>'attachment_id',(body->>'version')::bigint,event_id,body,body-'readiness_evidence' "
                "FROM inserted WHERE body->>'type'='comment_attachment' "
                "ON CONFLICT(session_id,attachment_id) DO UPDATE SET version=EXCLUDED.version,event_id=EXCLUDED.event_id, "
                "body=EXCLUDED.body,summary=EXCLUDED.summary WHERE attachments.version<EXCLUDED.version RETURNING event_id), "
                "projected AS (INSERT INTO orders(session_id,order_id,state,approval_needed,amount,updated_ts_ms,body) "
                "SELECT body->>'session_id',body->>'order_id',body->>'state',(body->>'approval_needed')::boolean, "
                "(body->>'amount')::bigint,(body->>'ts_ms')::bigint,body FROM latest "
                "ON CONFLICT(session_id,order_id) DO UPDATE SET state=EXCLUDED.state, "
                "approval_needed=EXCLUDED.approval_needed,amount=EXCLUDED.amount, "
                "updated_ts_ms=EXCLUDED.updated_ts_ms,body=EXCLUDED.body RETURNING order_id) "
                "SELECT event_id,seq, (NOT EXISTS(SELECT 1 FROM intent_input) OR EXISTS(SELECT 1 FROM reserved) "
                "OR EXISTS(SELECT 1 FROM attachment_order_intents a JOIN intent_input i "
                "ON a.comment_id=i.comment_id AND a.order_id=i.order_id WHERE a.session_id=%s)) AS intent_ok "
                "FROM inserted",
                (json.dumps(intent_rows), sid, json.dumps(events), now_ms(), json.dumps(origins), sid),
            )
            inserted = await cur.fetchall()
            if any(not row["intent_ok"] for row in inserted):
                raise ValueError("comment already belongs to another order")
            if len(inserted) != len(events):
                from app.store import VersionTaken

                if intent is not None:
                    await self.reserve_attachment_order(sid, *intent)
                rows = await self._fetch(
                    "SELECT event_id,seq,body FROM events WHERE event_id=ANY(%s)",
                    ([event["event_id"] for event in events],),
                )
                canonical = {row["event_id"]: row["body"] for row in rows}
                if any(canonical.get(event["event_id"]) != event for event in events):
                    raise VersionTaken("order continuation event ID already has another canonical payload")
                inserted = rows
            self.notifier.notify()
            return {row["event_id"]: row["seq"] for row in inserted}

    def _active_attachment_connection(self):
        active = self._attachment_tx.get()
        return active[1] if active and active[0] is asyncio.current_task() else None

    @asynccontextmanager
    async def session_transaction(self, session_id):
        if self._active_attachment_connection() is not None:
            yield
            return
        if self.kind == "pg":
            async with self._writer_session() as conn, conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)), "
                    "set_config('keviq.attachment_write_scope','server',true)",
                    ("attachment:" + session_id,),
                )
                token = self._attachment_tx.set((asyncio.current_task(), conn))
                cache_token = self._attachment_cache.set({("write_scope",): "server"})
                try:
                    yield
                finally:
                    self._attachment_cache.reset(cache_token)
                    self._attachment_tx.reset(token)
        else:
            async with self._attachment_mutex:
                before = {
                    "_events": list(self._events),
                    "_by_id": dict(self._by_id),
                    "_cards": {k: list(v) for k, v in self._cards.items()},
                    "_comments": {
                        k: {cid: dict(row) for cid, row in v.items()} for k, v in self._comments.items()
                    },
                    "_orders": {k: dict(v) for k, v in self._orders.items()},
                    "_attachments": {k: dict(v) for k, v in self._attachments.items()},
                    "_binding_rows": dict(self._binding_rows),
                    "_binding_history": dict(self._binding_history),
                    "_source_rows": dict(self._source_rows),
                    "_consumed_inputs": set(self._consumed_inputs),
                    "_order_intents": dict(self._order_intents),
                }
                token = self._attachment_tx.set((asyncio.current_task(), True))
                try:
                    yield
                except BaseException:
                    for name, value in before.items():
                        setattr(self, name, value)
                    raise
                finally:
                    self._attachment_tx.reset(token)
                self.notifier.notify()

    async def attachment_binding(self, session_id):
        if self.kind == "memory":
            return copy.deepcopy(self._binding_rows.get(session_id))
        cache = self._attachment_cache.get() if self._active_attachment_connection() is not None else None
        if cache is not None and session_id in cache:
            return cache[session_id]
        rows = await self._fetch("SELECT body FROM attachment_bindings WHERE session_id = %s", (session_id,))
        result = rows[0]["body"] if rows else None
        if cache is not None:
            cache[session_id] = result
        return result

    async def attachment_bindings(self):
        if self.kind == "memory":
            return copy.deepcopy(list(self._binding_rows.values()))
        return [
            r["body"] for r in await self._fetch("SELECT body FROM attachment_bindings ORDER BY session_id")
        ]

    async def attachment_binding_revisions(self):
        """Polling needs revisions only; every decision still reads its authoritative binding under lock."""
        if self.kind == "memory":
            return {sid: row["revision"] for sid, row in self._binding_rows.items()}
        return {
            row["session_id"]: row["revision"]
            for row in await self._fetch(
                "SELECT session_id,(body->>'revision')::bigint AS revision FROM attachment_bindings"
            )
        }

    async def attachment_changes(self, after_seq):
        """Only sessions whose history changed; old sessions never delay a new session's timer."""
        if self.kind == "memory":
            return len(self._events), {
                e["session_id"]
                for e in self._events
                if e["seq"] > after_seq and e["session_id"] in self._binding_rows
            }
        rows = await self._fetch(
            "SELECT COALESCE(MAX(seq),%s) AS head, "
            "COALESCE(array_agg(DISTINCT session_id) FILTER (WHERE session_id IN "
            "(SELECT session_id FROM attachment_bindings)), ARRAY[]::text[]) AS sessions FROM events WHERE seq>%s",
            (after_seq, after_seq),
        )
        return rows[0]["head"], set(rows[0]["sessions"])

    async def attachment_history_head(self, session_id):
        if self.kind == "memory":
            return await self.attachment_binding(session_id), max(
                (e["seq"] for e in self._events if e["session_id"] == session_id), default=0
            )
        rows = await self._fetch(
            "SELECT body, COALESCE((SELECT MAX(seq) FROM events WHERE session_id=%s),0) AS head, "
            "COALESCE((SELECT MAX(seq) FROM events),0) AS global_head "
            "FROM attachment_bindings WHERE session_id=%s",
            (session_id, session_id),
        )
        if not rows:
            if self._active_attachment_connection() is not None:
                self._attachment_cache.get()[session_id] = None
            return None, 0
        if self._active_attachment_connection() is not None:
            self._attachment_cache.get()[session_id] = rows[0]["body"]
            self._attachment_cache.get()[("global_head",)] = rows[0]["global_head"]
        return rows[0]["body"], rows[0]["head"]

    async def attachment_global_head(self):
        cache = self._attachment_cache.get() if self._active_attachment_connection() is not None else None
        if cache is not None and ("global_head",) in cache:
            return cache[("global_head",)]
        return await self.head_seq()

    async def attachment_binding_history(self, session_id=None):
        if self.kind == "memory":
            return copy.deepcopy(
                [
                    row
                    for (sid, _), row in self._binding_history.items()
                    if session_id is None or session_id == sid
                ]
            )
        where, params = (" WHERE session_id=%s", (session_id,)) if session_id else ("", ())
        return [
            r["body"]
            for r in await self._fetch(
                "SELECT body FROM attachment_binding_history" + where + " ORDER BY session_id,revision",
                params,
            )
        ]

    async def put_attachment_binding(self, session_id, body):
        async with self.session_transaction(session_id):
            old = await self.attachment_binding(session_id)
            revision = (old or {}).get("revision", 0) + 1
            row = {**copy.deepcopy(body), "session_id": session_id, "revision": revision}
            if self.kind == "memory":
                self._binding_rows[session_id] = row
                self._binding_history[(session_id, revision)] = copy.deepcopy(row)
            else:
                await self._fetch(
                    "INSERT INTO attachment_binding_history(session_id,revision,body) VALUES (%s,%s,%s) RETURNING revision",
                    (session_id, revision, json.dumps(row)),
                )
                await self._fetch(
                    "INSERT INTO attachment_bindings(session_id,body) VALUES (%s,%s) "
                    "ON CONFLICT(session_id) DO UPDATE SET body=EXCLUDED.body RETURNING session_id",
                    (session_id, json.dumps(row)),
                )
                self._attachment_cache.get()[session_id] = row
            return row

    async def source_receipts(self, session_id):
        if self.kind == "memory":
            return copy.deepcopy(
                {k: v for k, v in self._source_rows.items() if v["session_id"] == session_id}
            )
        rows = await self._fetch(
            "SELECT event_id,body FROM attachment_source_receipts WHERE session_id=%s", (session_id,)
        )
        return {r["event_id"]: r["body"] for r in rows}

    async def record_source_receipt(self, event_id, body):
        if self.kind == "memory":
            old = self._source_rows.setdefault(event_id, copy.deepcopy(body))
            if old != body:
                raise ValueError("source receipt is immutable")
        else:
            inserted = await self._fetch(
                "INSERT INTO attachment_source_receipts(event_id,session_id,body) VALUES (%s,%s,%s) "
                "ON CONFLICT(event_id) DO NOTHING RETURNING event_id",
                (event_id, body["session_id"], json.dumps(body)),
            )
            if inserted:
                if self._active_attachment_connection() is not None:
                    self._attachment_cache.get()[("origin", event_id)] = body
                return
            rows = await self._fetch(
                "SELECT body FROM attachment_source_receipts WHERE event_id=%s", (event_id,)
            )
            if rows[0]["body"] != body:
                raise ValueError("source receipt is immutable")

    async def reserve_attachment_order(self, session_id, comment_id, order_id):
        if self.kind == "memory":
            if any(
                sid == session_id and other != comment_id and value == order_id
                for (sid, other), value in self._order_intents.items()
            ):
                raise ValueError("order identity already belongs to another comment")
            old = self._order_intents.setdefault((session_id, comment_id), order_id)
            if old != order_id:
                raise ValueError("comment already belongs to another order")
        else:
            inserted = await self._fetch(
                "INSERT INTO attachment_order_intents(session_id,comment_id,order_id) VALUES (%s,%s,%s) "
                "ON CONFLICT(session_id,comment_id) DO NOTHING RETURNING order_id",
                (session_id, comment_id, order_id),
            )
            if inserted:
                return
            rows = await self._fetch(
                "SELECT order_id FROM attachment_order_intents WHERE session_id=%s AND comment_id=%s",
                (session_id, comment_id),
            )
            if rows[0]["order_id"] != order_id:
                raise ValueError("comment already belongs to another order")

    async def attachments(self, session_id, *, include_evidence=False):
        if self.kind == "memory":
            return [
                {k: copy.deepcopy(v) for k, v in a.items() if include_evidence or k != "readiness_evidence"}
                for a in self._attachments.get(session_id, {}).values()
            ]
        column = "body" if include_evidence else "summary"
        rows = await self._fetch(
            f"SELECT {column} AS body FROM attachments WHERE session_id=%s ORDER BY attachment_id",
            (session_id,),
        )
        return [r["body"] for r in rows]

    async def attachment_events(self, session_id):
        if self.kind == "memory":
            rows = [
                e for e in self._events if e["session_id"] == session_id and e["type"] != "comment_attachment"
            ]
            latest_ids = {e["event_id"] for e in self._attachments.get(session_id, {}).values()}
            rows += [
                {k: v for k, v in e.items() if k != "readiness_evidence"}
                for e in self._events
                if e["event_id"] in latest_ids
            ]
            return sorted(rows, key=lambda e: e["seq"])
        rows = await self._fetch(
            "SELECT seq,ingested_ms,body FROM events WHERE session_id=%s AND type<>'comment_attachment' "
            "UNION ALL SELECT e.seq,e.ingested_ms,a.summary AS body FROM attachments a JOIN events e ON e.event_id=a.event_id "
            "WHERE a.session_id=%s ORDER BY seq",
            (session_id, session_id),
        )
        return [{**r["body"], "seq": r["seq"], "ingested_ms": r["ingested_ms"]} for r in rows]

    async def attachment_selection(self, session_id, attachment_id):
        if self.kind == "memory":
            return next(
                (
                    copy.deepcopy(e)
                    for e in self._events
                    if e["session_id"] == session_id
                    and e["type"] == "comment_attachment"
                    and e["attachment_id"] == attachment_id
                    and e["action"] in {"ready", "select"}
                ),
                None,
            )
        rows = await self._fetch(
            "SELECT body-'readiness_evidence' AS body FROM events WHERE session_id=%s "
            "AND type='comment_attachment' AND body->>'attachment_id'=%s "
            "AND body->>'action' IN ('ready','select') ORDER BY (body->>'version')::bigint LIMIT 1",
            (session_id, attachment_id),
        )
        return rows[0]["body"] if rows else None

    async def consumed_attachment_inputs(self, session_id):
        if self.kind == "memory":
            return {event_id for sid, event_id in self._consumed_inputs if sid == session_id}
        return {
            row["event_id"]
            for row in await self._fetch(
                "SELECT event_id FROM attachment_consumed_inputs WHERE session_id=%s", (session_id,)
            )
        }

    async def consume_attachment_input(self, session_id, event_id):
        if self.kind == "memory":
            self._consumed_inputs.add((session_id, event_id))
        else:
            await self._fetch(
                "INSERT INTO attachment_consumed_inputs(session_id,event_id) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING event_id",
                (session_id, event_id),
            )

    async def attachment_version(self, session_id, attachment_id, version):
        if self.kind == "memory":
            return next(
                (
                    copy.deepcopy(e)
                    for e in self._events
                    if e["session_id"] == session_id
                    and e["type"] == "comment_attachment"
                    and e["attachment_id"] == attachment_id
                    and e["version"] == version
                ),
                None,
            )
        rows = await self._fetch(
            "SELECT seq,ingested_ms,body FROM events WHERE session_id=%s AND type='comment_attachment' "
            "AND body->>'attachment_id'=%s AND (body->>'version')::bigint=%s",
            (session_id, attachment_id, version),
        )
        return (
            {**rows[0]["body"], "seq": rows[0]["seq"], "ingested_ms": rows[0]["ingested_ms"]}
            if rows
            else None
        )
