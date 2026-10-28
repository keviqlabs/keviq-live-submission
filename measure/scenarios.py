"""Integration replay on a running app + worker without a model key (`make integration`, tests/integration.sh).

    python -m measure.scenarios --base-url URL --worker-log PATH [--prefix smoke-integ-<ts>] [--speed 20]

Everything is synthetic (data/comments, data/sessions/demo1) and goes over HTTP into the store and the worker's
SSE stream, so it runs on PostgreSQL when the app does. Four sessions:

  <prefix>-orders  the comments of u014, u064 and u065 with their gold labels, then the customers' Messenger
                   replies through the sim webhook: missing size (c019), wrong customer, negated cancel (c169 and
                   "ko huỷ nha"), real cancel (c192, then CANCEL:<order_id>), keep.
  <prefix>-load    all 200 comments with their gold labels: the orders stream's consequence time per order label.
  <prefix>-shield  the 200 raw comments, no labels: the labels brain without a key (the shield hides, the model
                   batch falls back to `other`); how long a phone number stays visible.
  <prefix>-demo1   demo1 without its fx- fixtures: what the worker makes of the raw inputs without a model.

Labels in -orders and -load are the gold labels replayed, not model output, and carry action none (hides are
measured in -shield only); their cards are replayed as fx- so the labels and card brains leave those sessions
alone. No model is called, so every time here is worker + store, not model latency. CHECK rows set the exit code
(1 if one fails); MEASURE rows report numbers against targets and do not.

A read waits at the loop-end barrier of measure/c5_live (Watch.barrier, the same rule): the worker runs under
measure.c5_proc and its log (--worker-log) says how far it has finished the stream (measure/c5_progress); a read
returns once that count has passed the session's head with nothing pending and no background task left, never on
quiet alone. A read that times out keeps what is there and fails the barrier CHECK, with what was left.

Limit: the run shares the test database. An app + worker started on it during the run (`make smoke`, a second
`make integration`, a replay script) reads the same event stream from the seq it starts at, has not seen a
session's earlier fx- cards and labels its comments again. The "no second worker" CHECK in each session flags
that run; rerun it alone. tests/integration.sh also refuses a port that another app already answers on.
"""

import argparse
import asyncio
import json
import math
import tempfile
import time
from collections import Counter
from pathlib import Path

import httpx

from app.contracts import now_ms
from app.settings import ROOT
from connectors.sim import replay
from connectors.sim.messenger import webhook_body
from measure import c5_live, c5_proc
from measure.core import CYCLE_MS, c1, c5, pct, when
from orders.flow import order_id_for, thread_for
from worker.brain.shield import PHONE

LABELS = ROOT / "data" / "comments" / "labels.jsonl"
CARDS = ROOT / "data" / "comments" / "cards.jsonl"
DEMO1 = ROOT / "data" / "sessions" / "demo1"
CUSTOMERS = ("u014", "u064", "u065")
WAIT_S = 20.0


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def cards(fixture: bool) -> list[dict]:
    return [c | {"event_id": ("fx-" if fixture else "") + c["event_id"]} for c in jsonl(CARDS)]


def comment(row: dict) -> dict:
    return {
        "event_id": f"cm-{row['comment_id']}",
        "ts_ms": row["ts_ms"],
        "session_id": "x",
        "source": "replay",
        "v": 1,
        "type": "comment",
        "comment_id": row["comment_id"],
        "user_ref": row["user_ref"],
        "text": row["text"],
        "reply_to": None,
    }


def gold_label(row: dict) -> dict:
    """The row's gold label as the comments stream would post it (action none: hides are the shield's)."""
    o = row["order"]
    draft = o and {
        "sku": o["sku"],
        "variant": {"size": o["size"], "color": o["color"]},
        "qty": o["qty"],
        "referenced_card_id": o["referenced_card_id"],
        "reference_kind": o["reference_kind"],
    }
    cid = row["comment_id"]
    return {
        "event_id": f"gl-{cid}",
        "ts_ms": row["ts_ms"] + 500,
        "session_id": "x",
        "source": "replay",
        "v": 1,
        "type": "comment_labeled",
        "comment_id": cid,
        "label": row["label"],
        "order_draft": draft,
        "scam_signals": [],
        "action": "none",
        "latency_ms": 500,
        "caused_by": [f"cm-{cid}"],
    }


def labeled_session(users: tuple[str, ...] | None = None) -> list[dict]:
    rows = [r for r in jsonl(LABELS) if users is None or r["user_ref"] in users]
    return cards(fixture=True) + [e for r in rows for e in (comment(r), gold_label(r))]


def shield_session() -> list[dict]:
    return cards(fixture=False) + [comment(r) for r in jsonl(LABELS)]


def demo1_session() -> list[dict]:
    return replay.load(DEMO1 / "events.jsonl", fixtures=False)


def gold_of(rows: list[dict]) -> dict:
    return {"comments": {r["comment_id"]: {"label": r["label"], "has_phone": r["has_phone"]} for r in rows}}


# --- reading the events back


def caused(evs: list[dict], event_id: str) -> list[dict]:
    return [e for e in evs if event_id in (e.get("caused_by") or [])]


def about(evs: list[dict], event_id: str, order_id: str) -> list[dict]:
    """Consequences of `event_id` that touch `order_id` (an order transition or a message about it)."""
    return [
        e
        for e in caused(evs, event_id)
        if order_id in (e.get("order_id"), (e.get("payload") or {}).get("order_id"))
        or order_id in ((e.get("payload") or {}).get("order_ids") or [])
    ]


def states(evs: list[dict], order_id: str) -> list[str]:
    return [e["state"] for e in evs if e["type"] == "order" and e["order_id"] == order_id]


def last_order(evs: list[dict], order_id: str) -> dict | None:
    return next((e for e in reversed(evs) if e["type"] == "order" and e["order_id"] == order_id), None)


def sent(evs: list[dict], thread: str, template: str | None = None) -> list[dict]:
    return [
        e
        for e in evs
        if e["type"] == "message_out" and e["thread_ref"] == thread and template in (None, e["template"])
    ]


def new_orders(before: list[dict], after: list[dict]) -> list[str]:
    seen = {e["event_id"] for e in before}
    return [e["event_id"] for e in after if e["type"] == "order" and e["event_id"] not in seen]


def changed(before: list[dict], after: list[dict], mid: str) -> list[str]:
    """What customer message `mid` changed: its consequences and any order event that was not there before."""
    return sorted({e["event_id"] for e in caused(after, mid)} | set(new_orders(before, after)))


def second_worker(evs: list[dict], sid: str, gold_only: bool) -> list[str]:
    """Comments that another worker on the same database labeled (see the module limit).

    In a gold-only session (-orders, -load) every label that is not a replayed gold label is one. Elsewhere a
    comment gets at most one shield label and one batch label, so a comment with more than that was labeled twice."""
    labels = [e for e in evs if e["type"] == "comment_labeled"]
    if gold_only:
        return sorted({e["comment_id"] for e in labels if not e["event_id"].startswith(f"{sid}.gl-")})
    n = Counter(e["comment_id"] for e in labels)
    signals = {e["comment_id"] for e in labels if e["scam_signals"]}
    return sorted(c for c, k in n.items() if k > (2 if c in signals else 1))


def invariants(evs: list[dict]) -> list[str]:
    """Order-layer invariants over one session's events; each string is a violation.

    - CONFIRMED only by the customer, caused by CONFIRM:<order_id> from the order's own thread;
    - a customer CANCELLED only caused by CANCEL:<order_id> from the order's own thread;
    - every message about an order goes to that order's customer;
    - no model_call (the run has no key).
    The customer of o-<comment_id> is the comment's author, read from the comment, not from the order: the order
    layer uses the order's customer_ref itself, so taking it from there would let the code grade itself. An order
    whose customer_ref is not the author is a violation; an order with no comment in the session falls back to
    its customer_ref."""
    by_id = {e["event_id"]: e for e in evs}
    author = {order_id_for(e["comment_id"]): e["user_ref"] for e in evs if e["type"] == "comment"}
    owner = {e["order_id"]: thread_for(e["customer_ref"]) for e in evs if e["type"] == "order"}
    owner |= {oid: thread_for(u) for oid, u in author.items()}
    bad = []
    for e in evs:
        if e["type"] == "order" and e["order_id"] in author and e["customer_ref"] != author[e["order_id"]]:
            bad.append(
                f"{e['event_id']}: {e['order_id']} for {e['customer_ref']}, comment by {author[e['order_id']]}"
            )
        if e["type"] == "model_call":
            bad.append(f"{e['event_id']}: model_call in a run without a key")
        elif e["type"] == "order" and (
            e["state"] == "CONFIRMED" or (e["state"] == "CANCELLED" and e["actor"] == "customer")
        ):
            trig = by_id.get((e.get("caused_by") or [None])[0]) or {}
            want = ("CONFIRM:" if e["state"] == "CONFIRMED" else "CANCEL:") + e["order_id"]
            qr = ((trig.get("payload") or {}).get("quick_reply") or "").partition("#")[0]
            if (
                e["actor"] != "customer"
                or trig.get("type") != "message_in"
                or trig.get("thread_ref") != owner.get(e["order_id"])
                or qr != want
            ):
                bad.append(
                    f"{e['event_id']}: {e['state']} not caused by {want} from {owner.get(e['order_id'])}"
                )
        elif e["type"] == "message_out":
            p = e.get("payload") or {}
            for oid in [p["order_id"]] if p.get("order_id") else p.get("order_ids") or []:
                if oid in owner and owner[oid] != e["thread_ref"]:
                    bad.append(f"{e['event_id']}: {e['template']} about {oid} went to {e['thread_ref']}")
    return bad


# --- driving the app


class WorkerLog:
    """The worker's log file (tests/integration.sh sends its stdout there): its last C5PROC progress line and when
    it was read, as measure/c5_live Child keeps them for Watch.barrier. Whole lines only."""

    def __init__(self, path: Path):
        self.path, self.pos = Path(path), 0
        self.progress, self.progress_at = None, None

    def read(self) -> None:
        with self.path.open("rb") as f:
            f.seek(self.pos)
            chunk = f.read()
        whole = chunk[: chunk.rfind(b"\n") + 1]
        self.pos += len(whole)
        for line in whole.decode(errors="replace").splitlines():
            if not line.startswith(c5_proc.TAG):
                continue
            try:
                said = json.loads(line[len(c5_proc.TAG) :])
            except ValueError:
                continue
            if said.get("kind") == "progress":
                self.progress, self.progress_at = said, time.monotonic()

    async def follow(self) -> None:
        while True:
            self.read()
            await asyncio.sleep(0.1)


class SessionWatch(c5_live.Watch):
    """Watch.barrier on one session for the integration worker, which reads the whole stream (no --session)."""

    def __init__(self, read, sid: str):
        super().__init__(None, sid, None, None)
        self.read = read

    async def poll(self) -> None:
        self.seqs = [seq for seq, _ in await self.read(self.sid) if seq is not None]
        self.polls += 1

    def at_head(self, p: dict, head: int | None) -> bool:
        """Its watermark past this session's head, on other sessions' events, is done with this one: not behind."""
        done = p.get("done_seq")
        return head is None or (done is not None and done >= head)


class Run:
    def __init__(self, http: httpx.AsyncClient, base_url: str, speed: float, worker_log: Path | None = None):
        self.http, self.base_url, self.speed = http, base_url, speed
        self.rows: list[tuple[str, str, bool | None, str]] = []  # (kind, name, ok, detail)
        self.worker = WorkerLog(worker_log) if worker_log else None
        self.barriers: list[tuple[str, dict]] = []  # (session, Watch.barrier result) of each read

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append(("CHECK", name, bool(ok), detail))

    def measure(self, name: str, detail: str, ok: bool | None = None) -> None:
        self.rows.append(("MEASURE", name, ok, detail))

    async def replay(self, target: str, events: list[dict]) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "events.jsonl"
            path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
            args = argparse.Namespace(
                file=str(path), session=target, target=target, speed=self.speed, no_fixtures=False,
                base_url=self.base_url,
            )  # fmt: skip
            await replay.run(args)

    async def read(self, sid: str) -> list[tuple[int | None, dict]]:
        r = await self.http.get(f"/api/sessions/{sid}/events", params={"cursor": 0, "follow": "false"})
        r.raise_for_status()
        rows, seq = [], None
        for line in r.text.splitlines():
            if line.startswith("id:"):
                seq = int(line[3:])
            elif line.startswith("data:"):
                rows.append((seq, json.loads(line[5:])))
                seq = None
        return rows

    async def events(self, sid: str) -> list[dict]:
        return [ev for _, ev in await self.read(sid)]

    async def wait_for(self, sid: str, done, limit_s: float = WAIT_S) -> list[dict]:
        end = time.monotonic() + limit_s
        while True:
            evs = await self.events(sid)
            if done(evs) or time.monotonic() > end:
                return evs
            await asyncio.sleep(0.2)

    async def settle(self, sid: str, limit_s: float = 60.0) -> list[dict]:
        """The session's events once the worker has finished them, by its own count (measure/c5_live Watch.barrier),
        never on quiet alone. At `limit_s` it returns what is there, and barrier_check fails that read."""
        if self.worker is None:
            raise SystemExit("settle needs the worker's log (--worker-log)")
        self.worker.read()
        watch = SessionWatch(self.read, sid)
        watching, following = asyncio.create_task(watch.run()), asyncio.create_task(self.worker.follow())
        try:
            row = await watch.barrier(self.worker, [], timeout_s=limit_s)
        finally:
            following.cancel()
            await watch.halt(watching)
        self.barriers.append((sid, row))
        return await self.events(sid)

    def barrier_check(self) -> None:
        left = [f"{sid} {r['status']} after {r['waited_ms']} ms: behind {r.get('behind')}, pending "
                f"{r.get('pending')}, running {r.get('running')}, background {r.get('background')}"
                for sid, r in self.barriers if r["status"] != "SETTLED"]  # fmt: skip
        waited = [r["waited_ms"] for _, r in self.barriers]
        self.check(
            f"every read waited until the worker had finished the session, by its count ({len(waited)} reads)",
            bool(waited) and not left,
            "; ".join(left[:3]) or (f"longest wait {max(waited)} ms" if waited else "no read"),
        )

    async def say(
        self, sid: str, thread: str, text: str | None = None, quick_reply: str | None = None
    ) -> str:
        """One customer message through the sim webhook; returns the message_in event_id."""
        await asyncio.sleep(0.005)  # distinct ts_ms, so distinct message ids
        body = webhook_body(thread, now_ms(), text=text, quick_reply=quick_reply)
        r = await self.http.post(f"/api/sessions/{sid}/sim/messenger", json=body)
        r.raise_for_status()
        return r.json()["events"][0]["event_id"]

    def invariants(self, label: str, evs: list[dict]) -> None:
        bad = invariants(evs)
        self.check(f"{label}: order-layer invariants hold on {len(evs)} events", not bad, "; ".join(bad[:3]))

    def one_worker(self, label: str, sid: str, evs: list[dict], gold_only: bool) -> None:
        extra = second_worker(evs, sid, gold_only)
        self.check(
            f"{label}: no second worker labeled the session (shared test database)",
            not extra,
            f"labeled again: {len(extra)} comments {extra[:5]}",
        )


async def orders(run: Run, sid: str) -> None:
    """Missing size, wrong customer, negated and real cancel, keep: the customers of c009, c019, c032."""
    await run.replay(sid, labeled_session(CUSTOMERS))
    u014, u064, u065 = (thread_for(u) for u in CUSTOMERS)
    evs = await run.wait_for(
        sid,
        lambda evs: (
            sent(evs, u064, "ask_variant") and sent(evs, u065, "ask_cancel") and states(evs, "o-c009")
        ),
    )
    evs = await run.settle(sid)
    asks = sent(evs, u064, "ask_variant")
    run.check(
        "c019 'lấy đôi này' (3 sizes): asks u064 which size, no order yet", asks and not states(evs, "o-c019")
    )
    run.check(
        "c009 'lấy 2 đôi size 40' drafts o-c009", states(evs, "o-c009"), "->".join(states(evs, "o-c009"))
    )
    c009_state = (last_order(evs, "o-c009") or {}).get("state")
    c169 = [e["event_id"] for e in evs if e["type"] == "comment_labeled" and e["comment_id"] == "c169"]
    run.check(
        "c169 'đừng huỷ đơn em nha shop' (negated, label other): no message, o-c009 not cancelled",
        not any(caused(evs, i) for i in c169)
        and not sent(evs, u014, "ask_cancel")
        and "CANCELLED" not in states(evs, "o-c009"),
        f"{len(c169)} labels of c169",
    )
    cancel_ask = sent(evs, u065, "ask_cancel")
    replies = [
        q["payload"] for m in cancel_ask for q in m["payload"]["send_api"]["message"].get("quick_replies", [])
    ]
    run.check(
        "c192 'huỷ giúp em đôi size 39' (label other): ask_cancel to u065 offers CANCEL:o-c032, nothing cancelled",
        len(cancel_ask) == 1 and "CANCEL:o-c032" in replies and "CANCELLED" not in states(evs, "o-c032"),
        f"{len(cancel_ask)} ask_cancel, quick replies {replies}",
    )

    before = evs
    mid = await run.say(sid, u065, text="size 40 nha")
    evs = await run.settle(sid)
    got = changed(before, evs, mid)
    run.check(
        "another customer's 'size 40 nha' with c019's question open changes nothing (no reply, no order event)",
        not got,
        f"changed {got}",
    )

    mid = await run.say(sid, u064, text="size 39 nha shop")
    evs = await run.wait_for(sid, lambda evs: set(states(evs, "o-c019")) & {"CONFIRM_SENT", "NEEDS_SELLER"})
    o = last_order(evs, "o-c019") or {}
    line = (o.get("lines") or [{}])[0]
    run.check(
        "u064 'size 39 nha shop' drafts o-c019 size 39 and sends confirm_order to u064",
        o.get("state") == "CONFIRM_SENT"
        and (line.get("variant") or {}).get("size") == "39"
        and sent(evs, u064, "confirm_order"),
        "->".join(states(evs, "o-c019")),
    )

    for thread, qr, oid in ((u065, "CONFIRM:o-c019", "o-c019"), (u065, "CANCEL:o-c009", "o-c009")):
        before = evs
        mid = await run.say(sid, thread, quick_reply=qr)
        evs = await run.settle(sid)
        got = [e.get("template") or e.get("state") for e in caused(evs, mid)]
        run.check(
            f"{qr} from another customer ({thread}) is refused: no order event, nothing about {oid}",
            not new_orders(before, evs) and not about(evs, mid, oid),
            f"replies {got}",
        )

    for text, qr in (("ko huỷ nha shop", None), (None, "KEEP:o-c009")):
        mid = await run.say(sid, u014, text=text, quick_reply=qr)
        evs = await run.settle(sid)
        got = [e.get("template") or e.get("state") for e in caused(evs, mid)]
        run.check(
            f"u014 {text or qr!r} with o-c009 open: o-c009 unchanged",
            (last_order(evs, "o-c009") or {}).get("state") == c009_state,
            f"replies {got}",
        )

    mid = await run.say(sid, u064, quick_reply="CONFIRM:o-c019")
    evs = await run.wait_for(sid, lambda evs: "CONFIRMED" in states(evs, "o-c019"))
    o = last_order(evs, "o-c019") or {}
    card = next(
        (e for e in evs if e["type"] == "card" and (e["card_id"], e["version"]) == ("card-01", 1)), {}
    )
    receipt = o.get("receipt") or {}
    run.check(
        "u064 CONFIRM:o-c019 confirms; receipt has card-01 v1 price and image; receipt sent to u064",
        o.get("state") == "CONFIRMED"
        and o.get("actor") == "customer"
        and (receipt.get("price"), receipt.get("image_url")) == (card.get("price"), card.get("image_url"))
        and sent(evs, u064, "receipt"),
        f"receipt {receipt.get('card_id')} v{receipt.get('card_version')} {receipt.get('price')}",
    )

    mid = await run.say(sid, u065, text="huỷ đơn giúp em")
    evs = await run.settle(sid)
    got = [e.get("template") or e.get("state") for e in caused(evs, mid)]
    run.check(
        "u065 free text 'huỷ đơn giúp em' cancels nothing",
        "CANCELLED" not in states(evs, "o-c032"),
        f"replies {got}",
    )

    mid = await run.say(sid, u065, quick_reply="CANCEL:o-c032")
    evs = await run.wait_for(
        sid, lambda evs: "CANCELLED" in states(evs, "o-c032") and sent(evs, u065, "order_cancelled")
    )
    o = last_order(evs, "o-c032") or {}
    run.check(
        "u065 CANCEL:o-c032 cancels it (actor customer) and sends order_cancelled to u065",
        o.get("state") == "CANCELLED" and o.get("actor") == "customer" and sent(evs, u065, "order_cancelled"),
    )
    mid = await run.say(sid, u065, quick_reply="CANCEL:o-c032")
    evs = await run.settle(sid)
    run.check(
        "a repeated CANCEL:o-c032 makes no second transition", states(evs, "o-c032").count("CANCELLED") == 1
    )
    run.invariants("orders", evs)
    run.one_worker("orders", sid, evs, gold_only=True)


async def load(run: Run, sid: str) -> None:
    """All 200 comments with gold labels: each order label with a card should get an orders-stream consequence."""
    await run.replay(sid, labeled_session())
    evs = await run.settle(sid, limit_s=120.0)
    labels = [
        e
        for e in evs
        if e["type"] == "comment_labeled"
        and e["label"] == "order"
        and (e["order_draft"] or {}).get("referenced_card_id")
    ]
    dts, missing = [], []
    for lb in labels:
        after = [when(c) - when(lb) for c in caused(evs, lb["event_id"])]
        (dts.append(min(after)) if after else missing.append(lb["comment_id"]))
    within = sum(dt <= CYCLE_MS for dt in dts)
    last = {e["order_id"]: e for e in evs if e["type"] == "order"}  # last event per order
    finals = Counter(e["state"] for e in last.values())
    unresolved = sum(1 for e in evs if e["type"] == "comment_labeled" and e["label"] == "order") - len(labels)
    run.check(
        f"load: every order label with a card gets an orders-stream consequence ({len(labels)} labels)",
        not missing,
        f"missing {missing[:5]}",
    )
    run.measure(
        "load: order label -> first consequence (worker + PG, no model)",
        f"{within}/{len(labels)} within {CYCLE_MS} ms; p50 {pct(dts, 50)} ms, p95 {pct(dts, 95)} ms, max "
        f"{max(dts) if dts else None} ms; final states {dict(finals)}; asks "
        f"{Counter(e['template'] for e in evs if e['type'] == 'message_out')}; {unresolved} order labels with no "
        "card (the labels brain's question, not run here)",
        within == len(labels),
    )
    waiting = [e for e in last.values() if e["state"] == "NEEDS_SELLER"]
    run.measure(
        f"load: why orders wait for the seller ({len(waiting)} of {len(last)} final states are NEEDS_SELLER)",
        " | ".join(f"{n} x {r}" for r, n in Counter(e.get("reason") for e in waiting).most_common(8)),
    )
    ts = [r["ts_ms"] for r in jsonl(LABELS)]
    span_min = (max(ts) - min(ts)) / 60000
    seller = sum(e["type"] == "order" and e["actor"] == "seller" for e in evs)
    run.measure(
        "load: seller interventions per hour (C5) are not measured here",
        f"seller order decisions in this replay: {seller} (nobody acts as the seller). NEEDS_SELLER is a raw state"
        f" (a queue entry), not an intervention: {len(waiting)} entries over {span_min:.1f} min of gold source "
        f"time would be {len(waiting) / span_min * 60:.0f}/h only if each took exactly one seller tap, which "
        "nothing here measures",
    )
    run.check(
        "load: no CONFIRMED and no CANCELLED without a customer reply",
        not ({"CONFIRMED", "CANCELLED"} & set(finals)),
    )
    run.invariants("load", evs)
    run.one_worker("load", sid, evs, gold_only=True)


async def shield(run: Run, sid: str) -> None:
    """The 200 raw comments without labels: the labels brain's shield on PG through the worker, no key.

    The shield's rule is digits (worker/brain/shield.py); a phone spelled out in words is the model's to catch,
    so without a key it stays visible. The CHECK holds the shield to its own rule; the MEASURE is C5 on all of
    the gold phones, spelled ones included."""
    rows = jsonl(LABELS)
    await run.replay(sid, shield_session())
    evs = await run.settle(sid, limit_s=120.0)
    hidden = {e["comment_id"] for e in evs if e["type"] == "comment_labeled" and e["action"] == "hide"}
    phones = {r["comment_id"] for r in rows if r["has_phone"]}
    digits = {r["comment_id"] for r in rows if r["has_phone"] and PHONE.search(r["text"])}
    links = {r["comment_id"] for r in rows if r["has_link"]}
    labeled = Counter(
        e["label"] for e in {e["comment_id"]: e for e in evs if e["type"] == "comment_labeled"}.values()
    )
    run.check(
        f"shield: every phone written in digits is hidden ({len(digits)} of {len(phones)} gold phones)",
        digits <= hidden,
        f"not hidden {sorted(digits - hidden)[:5]}",
    )
    r = c5(evs, gold_of(rows))
    comments = {e["comment_id"]: e for e in evs if e["type"] == "comment"}
    first_hide = {}
    for e in evs:
        if e["type"] == "comment_labeled" and e["action"] == "hide":
            first_hide.setdefault(e["comment_id"], when(e))
    vis = [(first_hide[c] - when(comments[c])) / 1000 for c in digits if c in first_hide and c in comments]
    run.measure(
        "shield: C5 phone visible (comment ingest -> first hide label ingest; worker + PG, no model)",
        f"max {r['phone_visible_max_s']} s on all n={r['phone_n']} (target <= 5 s); digits: max {max(vis, default=None)}"
        f" s, p95 {pct(vis, 95)} s on n={len(vis)}; never hidden without a model: {sorted(phones - hidden)}; links "
        f"hidden {len(links & hidden)}/{len(links)}; labels {dict(labeled)} (no key: the model batch falls back to "
        "other)",
        r["phone_visible_max_s"] is not None and r["phone_visible_max_s"] <= 5,
    )
    run.invariants("shield", evs)
    run.one_worker("shield", sid, evs, gold_only=False)


async def demo1(run: Run, sid: str) -> None:
    """demo1 without fixtures: raw comments, product_seen, speech and two customer messages, no key."""
    await run.replay(sid, demo1_session())
    evs = await run.settle(sid)
    gold = json.loads((DEMO1 / "gold.json").read_text())
    r, k = c5(evs, gold), c1(evs)
    run.check(
        "demo1 (no fixtures): the phone comment is hidden",
        r["phone_n"] and r["phone_visible_max_s"] is not None and not math.isinf(r["phone_visible_max_s"]),
        f"max {r['phone_visible_max_s']} s on n={r['phone_n']}",
    )
    types = Counter(e["type"] for e in evs)
    run.measure(
        "demo1 (no fixtures, no key): C1 consequence within one cycle",
        f"{k['covered']}/{k['n']} (no cards without the model; not a model result); events {dict(types)}",
    )
    run.invariants("demo1", evs)
    run.one_worker("demo1", sid, evs, gold_only=False)


async def main_async(args, prefix: str) -> Run:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=10) as http:
        run = Run(http, args.base_url, args.speed, worker_log=Path(args.worker_log))
        for name, scenario in (("orders", orders), ("load", load), ("shield", shield), ("demo1", demo1)):
            if not args.only or name in args.only:
                await scenario(run, f"{prefix}-{name}")
        run.barrier_check()
    return run


def report(run: Run, prefix: str, args) -> int:
    print(
        f"\nintegration {prefix} (speed {args.speed}; no model key: labels are gold replays or the no-key fallback)"
    )
    for kind, name, ok, detail in run.rows:
        marks = (
            {True: "PASS", False: "FAIL"} if kind == "CHECK" else {True: "MET", False: "MISS", None: "----"}
        )
        print(f"{kind:7} {marks[ok]:4} {name}" + (f"\n             {detail}" if detail else ""))
    checks = [ok for kind, _, ok, _ in run.rows if kind == "CHECK"]
    print(f"RESULT {sum(checks)}/{len(checks)} checks pass")
    if args.json:
        rows = {"prefix": prefix, "speed": args.speed, "rows": run.rows}
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0 if checks and all(checks) else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="measure.scenarios")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--worker-log", required=True, help="the log of the worker, run under measure.c5_proc")
    p.add_argument("--prefix", help="session prefix (default smoke-integ-<epoch>, a test-session prefix)")
    p.add_argument("--speed", type=float, default=20.0)
    p.add_argument("--only", nargs="*", help="orders load shield demo1")
    p.add_argument("--json", help="also write the rows as JSON here")
    args = p.parse_args(argv)
    prefix = args.prefix or f"smoke-integ-{int(time.time())}"
    return report(asyncio.run(main_async(args, prefix)), prefix, args)


if __name__ == "__main__":
    raise SystemExit(main())
