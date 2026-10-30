"""C5 runner: the shop and the test customers, written down as assumptions (they are not measured behaviour).

Shop, one tap each, `delay_s` after it sees the event:
- price_proposal: accept it through the price route when the item in the host's hands at the line (windows.json)
  is the card's item and the heard price is right for it (Gold.right_price, the scorer's rule); type the gold price
  at the end of the line when it is the card's item but the heard price is not right and the card does not hold
  that price; otherwise drop it (no call). A 409 rereads the card and tries once more.
- order in NEEDS_SELLER: reject it when the reason is stock; otherwise approve it at the live card price (the
  order's card version when the live one has no price), or at the gold price when neither has one.
Customer: each messenger-k line goes out at t0 + not_before_ms, once the message it answers (`after`) was sent for
its order: a quick reply ("CONFIRM:<order>") or the text.
"""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.contracts import now_ms
from connectors.sim.messenger import webhook_body
from orders.flow import order_id_for, thread_for

REASONS = (  # NEEDS_SELLER reason -> cause, first match wins
    ("> stock", "over_stock"),
    ("no price", "no_price"),
    ("!= live card price", "price_changed"),
    ("no longer lists", "variant"),
    ("confirms again", "price_changed"),
    ("discount", "discount"),
    ("threshold", "amount"),
    ("retired", "card_state"),
    ("size", "variant"),
    ("color", "variant"),
    ("variant", "variant"),
)


def cause(reason: str | None) -> str:
    text = (reason or "").lower()
    return next((c for key, c in REASONS if key in text), "other")


@dataclass
class Gold:
    """windows.json (committed) and the test1 product prices (local gold): what the host holds and charges."""

    windows: dict
    products: dict  # sku -> {"price_first", "price_fixed", ...}

    @classmethod
    def load(cls, c5_dir: Path, labels_path: Path) -> "Gold":
        return cls(
            json.loads((c5_dir / "windows.json").read_text()), json.loads(labels_path.read_text())["products"]
        )

    def held_at(self, off_ms: int) -> str | None:
        """Item in the host's hands at this loop offset (None between items)."""
        return next(
            (s["product"] for s in self.windows["segments"] if s["from_ms"] <= off_ms < s["to_ms"]), None
        )

    def card_item(self, off_ms: int) -> str | None:
        """Item a card opened at this offset stands for: an item's span starts at its change band, where the
        camera first sees it, and runs to the next item's band."""
        segs = self.windows["segments"]
        for j, s in enumerate(segs):
            start = s["band_ms"][0] if s["band_ms"] else s["from_ms"]
            end = (
                segs[j + 1]["band_ms"][0]
                if j + 1 < len(segs) and segs[j + 1]["band_ms"]
                else (segs[j + 1]["from_ms"] if j + 1 < len(segs) else self.windows["loop_ms"])
            )
            if start <= off_ms < end:
                return s["product"]
        return None

    def prices(self, sku: str | None) -> set[int]:
        p = self.products.get(sku) or {}
        return {x for x in (p.get("price_first"), p.get("price_fixed")) if x is not None}

    def price_at(self, sku: str, off_ms: int) -> int | None:
        """Gold price at this offset: the fixed price from the first time it was said, else the first price."""
        p, said = self.products.get(sku) or {}, self.windows["price_changes"].get(sku)
        if said and off_ms >= said[0] and p.get("price_fixed") is not None:
            return p["price_fixed"]
        return p.get("price_first")

    def right_price(self, sku: str | None, heard: int | None, start: int, end: int) -> bool:
        """The one rule the shop and the scorer share (ruling 03:48): the gold price at the end of the line, or either
        gold price of the item when the line [start, end] overlaps the window where the host says the change."""
        said = self.windows["price_changes"].get(sku)
        return (
            sku is not None
            and heard is not None
            and (
                heard == self.price_at(sku, end)
                or bool(said and start <= said[1] and end >= said[0] and heard in self.prices(sku))
            )
        )


@dataclass
class Feed:
    """Every event of the session seen so far, in seq order, with the indexes the actors need."""

    events: list = field(default_factory=list)
    by_id: dict = field(default_factory=dict)
    cards: dict = field(default_factory=dict)  # card_id -> versions in seq order
    sent: dict = field(default_factory=dict)  # (template, order_id) -> first message_out ts
    cursor: int = 0

    def add(self, seq: int | None, ev: dict) -> None:
        self.cursor = seq or self.cursor
        self.events.append(ev)
        self.by_id[ev["event_id"]] = ev
        if ev["type"] == "card":
            self.cards.setdefault(ev["card_id"], []).append(ev)
        elif ev["type"] == "message_out":
            payload = ev.get("payload") or {}
            for oid in [payload.get("order_id"), *payload.get("order_ids", [])]:
                self.sent.setdefault((ev["template"], oid), ev["ts_ms"])

    def version(self, card_id: str, version: int) -> dict | None:
        return next((c for c in self.cards.get(card_id, []) if c["version"] == version), None)

    def latest(self, card_id: str) -> dict | None:
        versions = self.cards.get(card_id)
        return max(versions, key=lambda c: c["version"]) if versions else None

    def first(self, card_id: str) -> dict | None:
        versions = self.cards.get(card_id)
        return min(versions, key=lambda c: c["version"]) if versions else None

    def card_versions(self) -> list[dict]:
        return [c for vs in self.cards.values() for c in vs]


@dataclass
class Loops:
    """Loop k starts at t0[k] on the session clock; a ts belongs to the last loop started at or before it."""

    t0: list = field(default_factory=list)

    def of(self, ts: int) -> tuple[int, int] | None:
        found = None
        for k, t in enumerate(self.t0, 1):
            if t <= ts:
                found = (k, t)
        return found

    def offset(self, ts: int) -> int | None:
        hit = self.of(ts)
        return ts - hit[1] if hit else None

    def span(self, line: dict | None) -> tuple[int | None, int | None]:
        """Loop offsets of a speech line's start and end (the end is the start when it has none)."""
        if not line or line.get("start_ms") is None:
            return None, None
        start = self.offset(line["start_ms"])
        end = self.offset(line["end_ms"]) if line.get("end_ms") is not None else None
        return start, start if end is None else end


@dataclass
class Shop:
    http: object
    sid: str
    gold: Gold
    feed: Feed
    loops: Loops
    delay_s: float = 4.0
    log: list = field(default_factory=list)
    tasks: set = field(default_factory=set)
    seen: set = field(default_factory=set)

    def see(self, ev: dict) -> None:
        if ev["event_id"] in self.seen:
            return
        if ev["type"] == "price_proposal":
            self._spawn(self.proposal(ev))
        elif (
            ev["type"] == "order" and ev["state"] == "NEEDS_SELLER" and ev.get("prev_state") != "NEEDS_SELLER"
        ):
            self._spawn(self.order(ev))
        else:
            return
        self.seen.add(ev["event_id"])

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def judge(self, p: dict) -> tuple[str, int | None, dict]:
        """("accept" | "type" | "drop", price, facts) for a proposal, from the gold only."""
        start, end = self.loops.span(self.feed.by_id.get(p["speech_event_id"]))
        card_off = self.card_off(p["card_id"])
        held = self.gold.held_at(start) if start is not None else None
        item = self.gold.card_item(card_off) if card_off is not None else None
        facts = {"held": held, "card_item": item, "right_card": bool(held) and held == item}
        if not facts["right_card"]:
            return "drop", None, facts
        if self.gold.right_price(held, p["heard_price"], start, end):
            return "accept", p["heard_price"], facts
        want = self.gold.price_at(held, end)
        latest = self.feed.latest(p["card_id"]) or {}
        if want is not None and latest.get("price") != want:
            return "type", want, facts
        return "drop", None, facts

    async def proposal(self, p: dict) -> None:
        await asyncio.sleep(self.delay_s)
        action, price, facts = self.judge(p)
        row = {"kind": "proposal", "event_id": p["event_id"], "card_id": p["card_id"], "action": action,
               "heard_price": p["heard_price"], "price": price, "seen_ms": p["ts_ms"], **facts}  # fmt: skip
        if action != "drop":
            body = {"price": price, "card_version": self.live_version(p)}
            if action == "accept":
                body["proposal_event_id"] = p["event_id"]
            path = f"/api/sessions/{self.sid}/cards/{p['card_id']}/price"
            r = await self.http.post(path, json=body)
            if r.status_code == 409 and "version" in r.text:
                await asyncio.sleep(0.5)
                body["card_version"] = self.live_version(p)
                r = await self.http.post(path, json=body)
            row |= {"status": r.status_code, "response": r.json() if r.status_code == 200 else r.text[:200]}
        row["done_ms"] = now_ms()
        self.log.append(row)

    def live_version(self, p: dict) -> int:
        latest = self.feed.latest(p["card_id"])
        return latest["version"] if latest else p["card_version"]

    def card_off(self, card_id: str) -> int | None:
        first = self.feed.first(card_id)
        frame = first["provenance"]["frame_ts_ms"] if first else None
        return self.loops.offset(frame) if frame is not None else None

    async def order(self, o: dict) -> None:
        await asyncio.sleep(self.delay_s)
        line = o["lines"][0]
        why = cause(o.get("reason"))
        body: dict = {"decision": "reject" if why == "over_stock" else "approve"}
        own = (self.feed.version(line["card_id"], line["card_version"]) or {}).get("price")
        live = (self.feed.latest(line["card_id"]) or {}).get("price")
        if body["decision"] == "approve" and live is not None and live != own:
            body["unit_price"] = live
        elif body["decision"] == "approve" and own is None:
            off = self.card_off(line["card_id"])
            item = self.gold.card_item(off) if off is not None else None
            body["unit_price"] = self.gold.price_at(item, off) if item else None
        r = await self.http.post(f"/api/sessions/{self.sid}/orders/{o['order_id']}/decision", json=body)
        self.log.append(
            {"kind": "order", "event_id": o["event_id"], "order_id": o["order_id"], "cause": why,
             "decision": body["decision"], "unit_price": body.get("unit_price"), "status": r.status_code,
             "response": None if r.status_code == 200 else r.text[:200], "seen_ms": o["ts_ms"], "done_ms": now_ms()}
        )  # fmt: skip


@dataclass
class Customers:
    http: object
    sid: str
    feed: Feed
    log: list = field(default_factory=list)
    tasks: set = field(default_factory=set)

    def start(self, rows: list[dict], t0: int, deadline: int, loop: int) -> None:
        for row in rows:
            task = asyncio.create_task(self.reply(row, t0, deadline, loop))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def reply(self, row: dict, t0: int, deadline: int, loop: int) -> None:
        oid = order_id_for(row["order_comment_id"])
        due = t0 + row["not_before_ms"]
        out = {"loop": loop, "msg_id": row["msg_id"], "order_id": oid, "expect": row["expect"],
               "situation": row["situation"], "due_ms": due}  # fmt: skip
        while now_ms() < due or (row["after"], oid) not in self.feed.sent:
            if now_ms() > deadline:
                self.log.append(out | {"sent": False, "why": f"no {row['after']} for {oid} by the loop end"})
                return
            await asyncio.sleep(0.2)
        qr = f"{row['quick_reply']}:{oid}" if row["quick_reply"] else None
        ts = now_ms()
        body = webhook_body(thread_for(row["user_ref"]), ts, text=row["text"], quick_reply=qr,
                            mid=f"m_{self.sid}_{row['msg_id']}")  # fmt: skip
        r = await self.http.post(f"/api/sessions/{self.sid}/sim/messenger", json=body)
        events = r.json().get("events", []) if r.status_code == 200 else []
        self.log.append(out | {"sent": r.status_code == 200, "status": r.status_code, "sent_ms": ts,
                               "message_in": events[0]["event_id"] if events else None})  # fmt: skip
