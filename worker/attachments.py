"""Owner-epoch frame completion ledger; queue replacement never becomes a terminal read."""

import asyncio
import logging

log = logging.getLogger("worker.attachments")
MAX_FRAMES = 1755


class FrameEvidence:
    def __init__(self, runner, credentials):
        self.runner, self.credentials = runner, credentials
        self.frames, self.terminals, self.labels, self.comments = {}, {}, {}, {}
        self.seen_frames, self.card_posts, self.closed = {}, {}, set()
        self.post_sequences = {}
        self.observed = {}
        self.submissions = {}
        self.task = None
        self.changed = False

    def receive(self, event):
        sid = event["session_id"]
        if sid not in self.credentials:
            return
        kind = event["type"]
        if kind == "frame":
            self.frames.setdefault(sid, {})[event["event_id"]] = event
        elif kind == "comment":
            self.comments[event["event_id"]] = event
        elif kind == "comment_labeled" and event["label"] == "order":
            self.labels.setdefault((sid, event["comment_id"]), event)
        elif kind == "comment_attachment":
            self.closed.add((sid, event["comment_id"]))
        self.wake()

    def posted(self, events, accepted=None):
        if accepted:
            self.post_sequences.update({row["event_id"]: row["seq"] for row in accepted})
        changed = False
        for event in events:
            if event["session_id"] not in self.credentials:
                continue
            changed = True
            if event["type"] == "product_seen":
                refs = [
                    ref
                    for ref in event.get("caused_by", [])
                    if ref in self.frames.get(event["session_id"], {})
                ]
                if len(refs) == 1:
                    self.seen_frames[event["event_id"]] = refs[0]
            elif event["type"] == "card":
                for ref in event.get("caused_by", []):
                    self.card_posts.setdefault(ref, set()).add(event["event_id"])
            elif event["type"] == "comment_labeled" and event["label"] == "order":
                self.labels.setdefault((event["session_id"], event["comment_id"]), event)
        if changed:
            self.wake()

    def frame_finished(self, event, output, *, read_error=False):
        if event["session_id"] not in self.credentials:
            return
        seen = [e for e in output if e["type"] == "product_seen"]
        if not seen:
            self.terminals[event["event_id"]] = {
                "frame_event_id": event["event_id"],
                "result": "read_error" if read_error else "no_product_seen",
            }
        # A recognition is terminal only after the separate product handler has posted its cards.
        self.wake()

    def product_finished(self, event):
        if event["session_id"] not in self.credentials:
            return
        frame_id = self.seen_frames.get(event["event_id"])
        cards = self.card_posts.get(event["event_id"], set())
        if frame_id and 1 <= len(cards) <= 2:
            self.terminals[frame_id] = {
                "frame_event_id": frame_id,
                "result": "product_seen",
                "product_seen_event_id": event["event_id"],
                "card_event_ids": sorted(cards),
            }
        self.wake()

    def wake(self):
        self.changed = True
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.evaluate())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def observe_through(self, sid, target):
        """Read a real stored prefix without dispatching handlers or claiming any frame completion."""
        from worker.runner import parse_sse

        cursor = max(self.runner.cursor, self.observed.get(sid, 0))
        while cursor < target:
            response = await self.runner.http.get(
                f"/api/sessions/{sid}/events", params={"cursor": cursor, "follow": "false"}
            )
            response.raise_for_status()
            previous = cursor
            async for seq, event in parse_sse(response.aiter_lines()):
                self.receive(event)
                cursor = max(cursor, seq)
            if cursor == previous:
                break
        self.observed[sid] = cursor
        return cursor

    async def evaluate(self):
        while self.changed:
            self.changed = False
            for key, label in list(self.labels.items()):
                sid, cid = key
                if key in self.closed:
                    continue
                comment = next(
                    (e for e in self.comments.values() if e["session_id"] == sid and e["comment_id"] == cid),
                    None,
                )
                if not comment:
                    continue
                frames = list(self.frames.get(sid, {}).values())
                required = sorted(
                    (e for e in frames if e["ts_ms"] <= comment["ts_ms"]), key=lambda e: e["ts_ms"]
                )
                if len(required) > MAX_FRAMES:
                    continue  # Never truncate a required prefix or rotate a session to evade its bound.
                after = next((e for e in frames if e["ts_ms"] > comment["ts_ms"]), None)
                evidence = None
                if required and after and all(e["event_id"] in self.terminals for e in required):
                    evidence = {
                        "after_frame_event_id": after["event_id"],
                        "handled_frames": [self.terminals[e["event_id"]] for e in required],
                    }
                if evidence is None:
                    # The durable comment/label already owns its app timer; polling cannot supply missing reads.
                    continue
                required_posts = [
                    ref
                    for record in evidence["handled_frames"]
                    if record["result"] == "product_seen"
                    for ref in [record["product_seen_event_id"], *record["card_event_ids"]]
                ]
                if label["event_id"] in self.post_sequences:
                    required_posts.append(label["event_id"])
                target = max(
                    (self.post_sequences.get(ref, float("inf")) for ref in required_posts), default=0
                )
                if target == float("inf"):
                    continue
                try:
                    observed = await self.observe_through(sid, target)
                except Exception as exc:
                    log.warning("attachment history read refused (%s)", type(exc).__name__)
                    continue
                actual_required = {
                    e["event_id"] for e in self.frames.get(sid, {}).values() if e["ts_ms"] <= comment["ts_ms"]
                }
                if actual_required != {e["event_id"] for e in required}:
                    self.changed = True
                    continue
                if observed < target or key in self.closed:
                    continue  # A successful post is not permission to invent an observed history cursor.
                credentials = self.credentials[sid]
                body = {
                    "worker_epoch": credentials["epoch"],
                    "comment_event_id": comment["event_id"],
                    "label_event_id": label["event_id"],
                    "observed_cursor": observed,
                }
                if evidence:
                    body["readiness_evidence"] = evidence
                signature = (observed, repr(evidence))
                if self.submissions.get(key) == signature:
                    continue
                try:
                    response = await self.runner.http.post(
                        f"/api/sessions/{sid}/attachments/system",
                        json=body,
                        headers={"Authorization": "Bearer " + credentials["system_token"]},
                    )
                    response.raise_for_status()
                    self.submissions[key] = signature
                    if response.json().get("attachment"):
                        self.closed.add(key)
                except Exception as exc:
                    # No authority/readiness fallback. Source history plus the app's original timer survives.
                    log.warning("attachment evaluation refused for session %s (%s)", sid, type(exc).__name__)
