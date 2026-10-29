"""Server-owned attachment history, source intake, and frame-evidence checks."""

import asyncio
import contextlib
import copy
import hashlib
import json
import logging

from fastapi import HTTPException

from app.attachment_auth import authorize, bearer_digest
from app.attachment_store import AttachmentDeadlinePassed
from app.attachment_validation import attachment_validator
from app.current import appearances, current_at
from orders.intent import cancel_request

MAX_RECORDS = 1755
MAX_BYTES = 1024 * 1024
log = logging.getLogger("attachments")


def plain(event):
    return {k: v for k, v in event.items() if k not in {"seq", "ingested_ms"}}


def digest(event):
    return hashlib.sha256(
        json.dumps(plain(event), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def stable_id(prefix, *parts):
    return prefix + hashlib.sha256("\0".join(map(str, parts)).encode()).hexdigest()[:48]


def eligible_labels(events):
    """One canonical source classification per comment; derived reconciliation labels are not new intents."""
    comments = {e["comment_id"]: e for e in events if e["type"] == "comment"}
    rows = {}
    attachment_ids = {e["event_id"] for e in events if e["type"] == "comment_attachment"}
    for label in events:
        if label["type"] != "comment_labeled" or label["label"] != "order":
            continue
        if label["comment_id"] not in comments or attachment_ids.intersection(label.get("caused_by", [])):
            continue
        cancellation = cancel_request(comments[label["comment_id"]].get("text"))
        if cancellation and cancellation["kind"] == "cancel":
            continue
        rows.setdefault(label["comment_id"], (comments[label["comment_id"]], label))
    return rows


class Attachments:
    def __init__(self, store_for, clock, *, sleep_scale=1):
        self.store_for, self.clock = store_for, clock
        self.sleep_scale = sleep_scale
        self.task = None
        self.initialized = False
        self.cursor, self.revisions, self.deadlines = 0, {}, {}
        self.prepared = {}

    @property
    def store(self):
        return self.store_for()

    async def configure(self, definition):
        """Trusted deployment/test hook only; never exposed as a request route."""
        sid = definition["session_id"]
        async with self.store.session_transaction(sid):
            old = await self.store.attachment_binding(sid)
            if old and any(old[k] != definition[k] for k in ("wait_ms", "stream_id", "ordered_media")):
                raise ValueError("binding rotation cannot reset the original wait or input stream")
            new_digests = {definition["system_digest"], *(g["digest"] for g in definition["sources"])}
            for other in await self.store.attachment_binding_history():
                if other["session_id"] != sid and new_digests.intersection(
                    {other["system_digest"], *(g["digest"] for g in other["sources"])}
                ):
                    raise ValueError("a capability must be scoped to one session")
                if (
                    other["session_id"] == sid
                    and other["epoch"] != definition["epoch"]
                    and new_digests.intersection(
                        {other["system_digest"], *(g["digest"] for g in other["sources"])}
                    )
                ):
                    raise ValueError("historical epoch credentials cannot be reassigned")
            if (
                old
                and old["epoch"] != definition["epoch"]
                and new_digests.intersection({old["system_digest"], *(g["digest"] for g in old["sources"])})
            ):
                raise ValueError("an epoch takeover requires fresh capabilities")
            row = {
                **definition,
                "registered_seq": (old or {}).get("registered_seq", await self.store.head_seq()),
            }
            result = await self.store.put_attachment_binding(sid, row)
        self.store.notifier.notify()
        return result

    async def authorize(self, binding, header, **scope):
        supplied = bearer_digest(header)
        active = {binding["system_digest"], *(g["digest"] for g in binding["sources"])} if binding else set()
        if supplied not in active:
            for old in await self.store.attachment_binding_history():
                if supplied in {old["system_digest"], *(g["digest"] for g in old["sources"])}:
                    raise HTTPException(403, "credential is revoked or belongs to another session")
        return authorize(binding, header, **scope)

    def ensure_running(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    async def initialize(self):
        if self.initialized:
            return
        self.cursor = await self.store.head_seq()
        for binding in await self.store.attachment_bindings():
            sid = binding["session_id"]
            self.revisions[sid] = binding["revision"]
            upcoming = await self.expire_due(sid)
            if upcoming is not None:
                self.deadlines[sid] = upcoming
        self.initialized = True

    async def start(self):
        """Recover durable pending work before accepting requests, then keep its original timers running."""
        await self.initialize()
        self.ensure_running()

    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def ingest(self, event, header, *, before_append=None):
        if event["type"] == "comment_attachment":
            raise HTTPException(403, "attachment events have a server-only writer")
        sid = event["session_id"]
        async with self.store.session_transaction(sid):
            binding, head = await self.store.attachment_history_head(sid)
            if binding is None:
                if header:
                    raise HTTPException(403, "scoped credentials cannot open an unregistered session")
                return None
            self.ensure_running()
            origin = await self.authorize(binding, header, kind=event["type"], source=event["source"])
            events, receipts = await self.history(sid, binding, head)
            existing = next((e for e in events if e["event_id"] == event["event_id"]), None)
            if existing:
                if digest(existing) != digest(event):
                    raise HTTPException(409, "event ID already has another canonical payload")
                return existing["seq"], False
            self.guard_input(event, events, receipts, binding)
            if before_append:
                self.store.prime_card_guard(sid, events)
                await before_append()
            async with self.store.source_write(
                {**origin, "role": "source", "binding_revision": binding["revision"]}
            ):
                result = await self.store.append(event)
                written, receipt = self.store.written_event(event["event_id"])
                if result[1] and written is not None and receipt is not None:
                    self.prepared[sid] = {
                        "head": result[0],
                        "revision": binding["revision"],
                        "events": [*events, written],
                        "receipts": {**receipts, event["event_id"]: receipt},
                    }
                return result

    async def history(self, sid, binding, head):
        prepared = self.prepared.get(sid)
        if prepared and prepared["head"] == head and prepared["revision"] == binding["revision"]:
            return list(prepared["events"]), prepared["receipts"]
        events = await self.store.attachment_events(sid)
        receipts = await self.store.source_receipts(sid)
        self.prepared[sid] = {
            "head": head,
            "revision": binding["revision"],
            "events": list(events),
            "receipts": receipts,
        }
        return events, receipts

    @staticmethod
    def guard_input(event, events, receipts, binding):
        by_id = {e["event_id"]: e for e in events}
        kind = event["type"]

        def parents(types):
            return [
                by_id[ref]
                for ref in event.get("caused_by", [])
                if ref in by_id and by_id[ref]["type"] in types and ref in receipts
            ]

        if kind == "comment" and any(
            e["type"] == "comment" and e["comment_id"] == event["comment_id"] for e in events
        ):
            raise HTTPException(409, "comment identity is already recorded")

        if kind == "frame" and binding["ordered_media"]:
            previous = [e["ts_ms"] for e in events if e["type"] == "frame"]
            if previous and event["ts_ms"] <= max(previous):
                raise HTTPException(409, "media prefix is closed; frame time must increase")
        elif kind == "product_seen":
            source = parents({"frame"})
            if len(source) != 1 or source[0]["ts_ms"] != event["frame_ts_ms"]:
                raise HTTPException(422, "recognition must name its admitted frame")
        elif kind == "comment_labeled":
            source = parents({"comment"})
            if len(source) != 1 or source[0]["comment_id"] != event["comment_id"]:
                raise HTTPException(422, "label must name its original comment")
        elif kind == "card":
            causes = parents({"product_seen", "speech", "card"})
            if not causes:
                raise HTTPException(422, "card must name its committed source evidence")
            old = [e for e in events if e["type"] == "card" and e["card_id"] == event["card_id"]]
            if old:
                latest = max(old, key=lambda e: e["version"])
                if event["version"] <= latest["version"]:
                    raise HTTPException(409, "card version must advance beyond committed history")
                if event["sku"] != latest["sku"]:
                    raise HTTPException(409, "SKU changes require the seller item route")
                if event["provenance"]["frame_ts_ms"] != latest["provenance"]["frame_ts_ms"] and not any(
                    e["type"] == "product_seen" and e["frame_ts_ms"] == event["provenance"]["frame_ts_ms"]
                    for e in causes
                ):
                    raise HTTPException(422, "new appearance must preserve its recognition frame")
                if (
                    latest["price"] is None
                    and event["price"] is not None
                    and not any(
                        e["type"] == "speech"
                        and e.get("final")
                        and e["event_id"] in event["provenance"]["speech_event_ids"]
                        for e in causes
                    )
                ):
                    raise HTTPException(409, "first source price requires admitted final seller speech")
            elif event["price"] is not None or event["status"] != "draft":
                raise HTTPException(422, "new recognition cards start as unpriced drafts")
            elif event["version"] != 1:
                raise HTTPException(409, "new card starts at version one")
            if not old and not any(
                e["type"] == "product_seen" and e["frame_ts_ms"] == event["provenance"]["frame_ts_ms"]
                for e in causes
            ):
                raise HTTPException(422, "initial card must preserve its frame evidence")
            if old and not any(e["type"] == "speech" for e in causes):
                priced = [e for e in old if e["price"] is not None]
                if priced and event["price"] != max(priced, key=lambda e: e["version"])["price"]:
                    raise HTTPException(409, "price changes require the seller price route")
        elif kind == "price_proposal":
            speech = parents({"speech"})
            card = max(
                (e for e in events if e["type"] == "card" and e["card_id"] == event["card_id"]),
                key=lambda e: e["version"],
                default=None,
            )
            if not any(e["event_id"] == event["speech_event_id"] for e in speech):
                raise HTTPException(422, "price proposal must name admitted speech")
            if (
                not card
                or card["version"] != event["card_version"]
                or card["price"] != event["current_price"]
            ):
                raise HTTPException(409, "price proposal does not describe the current same-session card")

    @staticmethod
    def snapshot(events, receipts, binding, comment, *, recovery_deadline=None):
        cards = [
            e
            for e in events
            if e["type"] == "card"
            and e["event_id"] in receipts
            and (recovery_deadline is None or e["ingested_ms"] <= recovery_deadline)
        ]
        current, previous = current_at(cards, comment["ts_ms"])
        history = appearances(cards)
        out = []
        for card in (current, previous):
            if card is None:
                continue
            appearance = next(
                (
                    e
                    for e in reversed(history)
                    if e["card_id"] == card["card_id"] and e["provenance"]["frame_ts_ms"] <= comment["ts_ms"]
                ),
                None,
            )
            if appearance is not None:
                out.append(
                    {
                        "card_id": card["card_id"],
                        "card_version": card["version"],
                        "card_event_id": card["event_id"],
                        "appearance_event_id": appearance["event_id"],
                        "appearance_frame_ts_ms": appearance["provenance"]["frame_ts_ms"],
                    }
                )
        return out

    @staticmethod
    def check_evidence(events, receipts, binding, comment, observed_cursor, evidence):
        if not evidence or not binding["ordered_media"]:
            return False
        by_id = {e["event_id"]: e for e in events}
        frames = [e for e in events if e["type"] == "frame"]
        cards_by_parent, seen_by_frame = {}, set()
        for event in events:
            if event["type"] == "card":
                for parent in event.get("caused_by", []):
                    cards_by_parent.setdefault(parent, []).append(event)
            elif event["type"] == "product_seen":
                seen_by_frame.update(event.get("caused_by", []))
        required = {e["event_id"] for e in frames if e["ts_ms"] <= comment["ts_ms"]}
        if len(required) > MAX_RECORDS or any(
            a["ts_ms"] >= b["ts_ms"] for a, b in zip(frames, frames[1:], strict=False)
        ):
            return False

        def ref(event_id, kind):
            event = by_id.get(event_id)
            origin = receipts.get(event_id)
            if event is None:
                raise HTTPException(422, "evidence reference is not stored")
            if event["type"] != kind or event["seq"] > observed_cursor or not origin:
                raise HTTPException(422, "evidence reference is outside admitted history")
            if origin.get("role") != "source" or not origin.get("binding_revision"):
                raise HTTPException(422, "frame completion must cite an admitted source-transport record")
            if (
                origin["stream_id"] != binding["stream_id"]
                or origin["session_id"] != comment["session_id"]
                or origin["digest"] != digest(event)
            ):
                raise HTTPException(422, "evidence reference belongs to another input stream")
            if kind in {"product_seen", "card"} and origin["epoch"] != binding["epoch"]:
                raise HTTPException(422, "unaccepted terminal outputs belong to an earlier worker epoch")
            return event

        after = ref(evidence["after_frame_event_id"], "frame")
        if after["ts_ms"] <= comment["ts_ms"]:
            return False
        records = {}
        for record in evidence["handled_frames"]:
            frame_id = record["frame_event_id"]
            frame = ref(frame_id, "frame")
            if frame_id in records:
                raise HTTPException(422, "duplicate frame claim")
            if frame["ts_ms"] > comment["ts_ms"]:
                raise HTTPException(422, "after-frame reads are not required evidence")
            records[frame_id] = record
            if record["result"] != "product_seen" and frame_id in seen_by_frame:
                return False
            if record["result"] == "product_seen":
                seen = ref(record["product_seen_event_id"], "product_seen")
                if frame_id not in seen.get("caused_by", []) or seen["frame_ts_ms"] != frame["ts_ms"]:
                    raise HTTPException(422, "recognition evidence belongs to another frame")
                outputs = cards_by_parent.get(seen["event_id"], [])
                if (
                    not outputs
                    or len(outputs) > 2
                    or {e["event_id"] for e in outputs} != set(record["card_event_ids"])
                ):
                    return False
                for card in outputs:
                    ref(card["event_id"], "card")
                if not any(e["provenance"]["frame_ts_ms"] == frame["ts_ms"] for e in outputs):
                    return False
        return required == set(records) and all(by_id[i]["seq"] <= observed_cursor for i in required)

    @staticmethod
    def resolve_reference(label, candidates, events):
        draft = label.get("order_draft") or {}
        kind = draft.get("reference_kind")
        if kind == "current_card":
            return candidates[0]["card_id"] if candidates else None
        if kind == "previous_card":
            return candidates[1]["card_id"] if len(candidates) == 2 else None
        if kind == "explicit_sku" and draft.get("sku") is not None:
            by_id = {e["event_id"]: e for e in events}
            matching = [c["card_id"] for c in candidates if by_id[c["card_event_id"]]["sku"] == draft["sku"]]
            return matching[0] if len(matching) == 1 else None
        return None

    async def system(self, sid, body, header):
        self.ensure_running()
        async with self.store.session_transaction(sid):
            binding, head = await self.store.attachment_history_head(sid)
            await self.authorize(binding, header, epoch=body["worker_epoch"])
            events, receipts = await self.history(sid, binding, head)
            by_id = {e["event_id"]: e for e in events}
            comment, label = by_id.get(body["comment_event_id"]), by_id.get(body["label_event_id"])
            if comment is None or label is None:
                raise HTTPException(404, "source comment or label is not in this session")
            pair = eligible_labels(events).get(comment.get("comment_id"))
            if (
                comment["type"] != "comment"
                or label["type"] != "comment_labeled"
                or not pair
                or pair[0]["event_id"] != comment["event_id"]
                or pair[1]["event_id"] != label["event_id"]
            ):
                raise HTTPException(409, "source identity is not the canonical final order label")
            if comment["event_id"] not in receipts or label["event_id"] not in receipts:
                raise HTTPException(422, "source history was not admitted by the configured transport")
            cursor = max((e["seq"] for e in events), default=0)
            observed = body["observed_cursor"]
            if (
                observed > await self.store.attachment_global_head()
                or max(comment["seq"], label["seq"]) > observed
            ):
                raise HTTPException(422, "observed cursor is future or omits the source label")
            aid = stable_id("att-", sid, label["event_id"])
            existing = next(
                (
                    e
                    for e in events
                    if e["type"] == "comment_attachment" and e["comment_id"] == comment["comment_id"]
                ),
                None,
            )
            if existing:
                if existing["attachment_id"] != aid:
                    raise HTTPException(409, "comment already has another attachment identity")
                return {
                    "attachment": plain(await self.store.attachment_version(sid, aid, 1)),
                    "duplicate": True,
                }
            deadline = comment["ingested_ms"] + binding["wait_ms"]
            now = self.clock()
            event = None
            if now < deadline:
                candidates = self.snapshot(events, receipts, binding, comment)
                target = self.resolve_reference(label, candidates, events)
                covered = self.check_evidence(
                    events, receipts, binding, comment, observed, body.get("readiness_evidence")
                )
                log.info(
                    "attachment evaluation: covered=%s resolved=%s remaining_ms=%s",
                    covered,
                    bool(target),
                    deadline - self.clock(),
                )
                if target and covered:
                    now = self.clock()
                    if now < deadline:
                        event = self.initial_expiry(
                            binding, comment, label, candidates, cursor, worker_epoch=binding["epoch"]
                        )
                        event.update(
                            outcome="ready",
                            action="ready",
                            selected_card_id=target,
                            reason="ordered media coverage and reference resolved",
                            readiness_evidence={
                                "observed_cursor": observed,
                                **copy.deepcopy(body["readiness_evidence"]),
                            },
                        )
            if event is None and self.clock() >= deadline:
                candidates = self.snapshot(events, receipts, binding, comment, recovery_deadline=deadline)
                event = self.initial_expiry(
                    binding, comment, label, candidates, cursor, worker_epoch=binding["epoch"]
                )
            if event is None:
                return {"pending": True, "deadline_ms": deadline}
            attachment_validator().validate(event)
            if len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_BYTES:
                raise HTTPException(413, "full attachment evidence exceeds the byte bound")
            # Provisioning and all source/seller writers use this same transaction lock.
            await self.authorize(await self.store.attachment_binding(sid), header, epoch=body["worker_epoch"])
            if event["outcome"] == "ready" and self.clock() >= deadline:
                event = self.initial_expiry(
                    binding,
                    comment,
                    label,
                    self.snapshot(events, receipts, binding, comment, recovery_deadline=deadline),
                    cursor,
                    worker_epoch=binding["epoch"],
                )
            try:
                await self.store.append(event)
            except AttachmentDeadlinePassed:
                event = self.initial_expiry(
                    binding,
                    comment,
                    label,
                    self.snapshot(events, receipts, binding, comment, recovery_deadline=deadline),
                    cursor,
                    worker_epoch=binding["epoch"],
                )
                await self.store.append(event)
            await self.reconcile(sid, [*events, event])
            return {"attachment": event, "duplicate": False}

    async def reconcile(self, sid, events=None):
        from app.attachment_orders import reconcile

        return await reconcile(self.store, sid, self.clock(), events)

    async def seller_decision(self, sid, aid, body):
        from app.attachment_orders import order_id_for

        async with self.store.session_transaction(sid):
            current = next((a for a in await self.store.attachments(sid) if a["attachment_id"] == aid), None)
            if current is None:
                raise HTTPException(404, "attachment is not in this session")
            expected = body["attachment_version"]
            accepted = await self.store.attachment_version(sid, aid, expected + 1)
            if accepted is not None:
                if accepted["action"] == body["decision"] and (
                    body["decision"] != "select" or accepted["selected_card_id"] == body["card_id"]
                ):
                    return {"attachment": plain(accepted), "events": [], "duplicate": True}
                raise HTTPException(409, "a different decision already used this version")
            events = await self.store.attachment_events(sid)
            if any(
                e["type"] == "order" and e["order_id"] == order_id_for(current["comment_id"]) for e in events
            ):
                raise HTTPException(409, "an order exists; use its approve/reject decision route")
            if current["version"] != expected or current["action"] == "reject":
                raise HTTPException(409, "attachment version changed or intent was rejected")
            selected = current["selected_card_id"]
            if body["decision"] == "select":
                if body["card_id"] not in {c["card_id"] for c in current["candidates"]}:
                    raise HTTPException(422, "selection is outside the frozen same-session snapshot")
                if current["outcome"] != "expired" or selected is not None:
                    raise HTTPException(409, "attachment identity is already selected")
                selected = body["card_id"]
            event = plain(await self.store.attachment_version(sid, aid, expected))
            evidence = next(
                (c["card_event_id"] for c in current["candidates"] if c["card_id"] == selected),
                current["label_event_id"],
            )
            event.update(
                version=expected + 1,
                event_id=stable_id("attv-", aid, expected + 1),
                ts_ms=self.clock(),
                actor="seller",
                action=body["decision"],
                selected_card_id=selected,
                reason="seller " + body["decision"],
                caused_by=[current["event_id"], evidence],
            )
            attachment_validator().validate(event)
            await self.store.append(event)
            effects = await self.reconcile(sid) if body["decision"] == "select" else []
            return {"attachment": event, "events": effects, "duplicate": False}

    def initial_expiry(self, binding, comment, label, candidates, cursor, *, worker_epoch=None):
        now = self.clock()
        aid = stable_id("att-", comment["session_id"], label["event_id"])
        deadline = comment["ingested_ms"] + binding["wait_ms"]
        event = {
            "type": "comment_attachment",
            "v": 1,
            "source": comment["source"],
            "ts_ms": now,
            "event_id": stable_id("attv-", aid, 1),
            "session_id": comment["session_id"],
            "caused_by": [comment["event_id"], label["event_id"]],
            "attachment_id": aid,
            "version": 1,
            "worker_epoch": worker_epoch,
            "comment_id": comment["comment_id"],
            "comment_event_id": comment["event_id"],
            "label_event_id": label["event_id"],
            "comment_ts_ms": comment["ts_ms"],
            "comment_ingested_ms": comment["ingested_ms"],
            "deadline_ms": deadline,
            "snapshot_at_ms": now,
            "snapshot_cursor": cursor,
            "candidates": candidates,
            "outcome": "expired",
            "action": "expire",
            "selected_card_id": None,
            "actor": "system",
            "reason": "original attachment deadline expired",
        }
        if now > deadline + 200:
            event["reason"] = "recovery-late: original attachment deadline expired"
        attachment_validator().validate(event)
        return event

    async def expire_due(self, sid):
        from app.attachment_orders import draft_attachment, order_id_for, qualified

        async with self.store.session_transaction(sid):
            binding, head = await self.store.attachment_history_head(sid)
            if binding is None:
                return None
            events, receipts = await self.history(sid, binding, head)
            existing = {e["comment_id"] for e in events if e["type"] == "comment_attachment"}
            next_deadline = None
            for comment_id, (comment, label) in eligible_labels(events).items():
                if comment_id in existing or label["event_id"] not in receipts:
                    continue
                deadline = comment["ingested_ms"] + binding["wait_ms"]
                now = self.clock()
                if now < deadline:
                    next_deadline = min(next_deadline or deadline, deadline)
                    continue
                cursor = max((e["seq"] for e in events), default=0)
                candidates = self.snapshot(events, receipts, binding, comment, recovery_deadline=deadline)
                event = self.initial_expiry(binding, comment, label, candidates, cursor)
                by_id = {e["event_id"]: e for e in events}
                frozen = [by_id[c["card_event_id"]] for c in candidates]
                prepared = []
                if len(frozen) == 2 and all(qualified(card) for card in frozen):
                    prepared = await draft_attachment(
                        self.store,
                        event,
                        events,
                        comment,
                        label,
                        frozen[0],
                        self.clock(),
                        [c["card_id"] for c in candidates],
                        persist=False,
                    )
                if prepared:
                    sequences = await self.store.append_order_batch(
                        prepared, intent=(comment_id, order_id_for(comment_id)), attachment=event
                    )
                    seq = sequences[event["event_id"]]
                else:
                    seq, _ = await self.store.append(event)
                events.append({**event, "seq": seq})
                if prepared:
                    events.extend({**item, "seq": sequences[item["event_id"]]} for item in prepared)
            await self.reconcile(sid, events)
            return next_deadline

    async def run(self):
        while True:
            try:
                await self.initialize()
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("attachment projector will retry (%s)", type(exc).__name__)
                await asyncio.sleep(0.05)

    async def tick(self):
        token = self.store.notifier.token()
        # A due deadline takes priority over polling other session changes.
        due = [sid for sid, at in self.deadlines.items() if at <= self.clock()]
        for sid in due:
            upcoming = await self.expire_due(sid)
            if upcoming is None:
                self.deadlines.pop(sid, None)
            else:
                self.deadlines[sid] = upcoming
        revisions = await self.store.attachment_binding_revisions()
        cursor, changed = await self.store.attachment_changes(self.cursor)
        revised = {}
        for sid, revision in revisions.items():
            if self.revisions.get(sid) != revision:
                revised[sid] = revision
                changed.add(sid)
        changed.update(sid for sid, at in self.deadlines.items() if at <= self.clock())
        for sid in changed:
            upcoming = await self.expire_due(sid)
            if upcoming is None:
                self.deadlines.pop(sid, None)
            else:
                self.deadlines[sid] = upcoming
        self.cursor = cursor
        self.revisions.update(revised)
        deadline = min(self.deadlines.values(), default=None)
        timeout = 30 if deadline is None else max((deadline - self.clock()) / 1000 / self.sleep_scale, 0)
        await self.store.notifier.wait(token, timeout)
