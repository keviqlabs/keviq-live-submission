"""Integration replay on a running app + worker without a model key (`make integration`, tests/integration.sh).

    python -m measure.scenarios --base-url URL [--prefix smoke-integ-<ts>] [--speed 20]

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
from measure.core import CYCLE_MS, c1, c5, pct, when
from orders.flow import order_id_for, thread_for
from worker.brain.shield import PHONE

LABELS = ROOT / "data" / "comments" / "labels.jsonl"
CARDS = ROOT / "data" / "comments" / "cards.jsonl"
DEMO1 = ROOT / "data" / "sessions" / "demo1"
CUSTOMERS = ("u014", "u064", "u065")
QUIET_S = 2.0  # how long to wait before deciding that a message produced nothing
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


def invariants(evs: list[dict]) -> list[str]:
    """Order-layer invariants over one session's events; each string is a violation.

    - CONFIRMED only by the customer, caused by CONFIRM:<order_id> from the order's own thread;
    - a customer CANCELLED only caused by CANCEL:<order_id> from the order's own thread;
    - every message about an order goes to that order's customer;
    - no model_call (the run has no key)."""
    by_id = {e["event_id"]: e for e in evs}
    owner = {order_id_for(e["comment_id"]): thread_for(e["user_ref"]) for e in evs if e["type"] == "comment"}
    owner |= {e["order_id"]: thread_for(e["customer_ref"]) for e in evs if e["type"] == "order"}
    bad = []
    for e in evs:
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


class Run:
    def __init__(self, http: httpx.AsyncClient, base_url: str, speed: float):
        self.http, self.base_url, self.speed = http, base_url, speed
        self.rows: list[tuple[str, str, bool | None, str]] = []  # (kind, name, ok, detail)

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

    async def events(self, sid: str) -> list[dict]:
        r = await self.http.get(f"/api/sessions/{sid}/events", params={"cursor": 0, "follow": "false"})
        r.raise_for_status()
        return [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]

    async def wait_for(self, sid: str, done, limit_s: float = WAIT_S) -> list[dict]:
        end = time.monotonic() + limit_s
        while True:
            evs = await self.events(sid)
            if done(evs) or time.monotonic() > end:
                return evs
            await asyncio.sleep(0.2)

    async def settle(self, sid: str, quiet: float = QUIET_S, limit_s: float = 60.0) -> list[dict]:
        """Wait until the session has had no new event for `quiet` seconds."""
        end, last, since = time.monotonic() + limit_s, -1, time.monotonic()
        while True:
            evs = await self.events(sid)
            if len(evs) != last:
                last, since = len(evs), time.monotonic()
            if time.monotonic() - since >= quiet or time.monotonic() > end:
                return evs
            await asyncio.sleep(0.2)

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
    run.check(
        "c169 'đừng huỷ đơn em nha shop' (negated, label other): no message, o-c009 not cancelled",
        not caused(evs, f"{sid}.gl-c169") and "CANCELLED" not in states(evs, "o-c009"),
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

    mid = await run.say(sid, u065, text="size 40 nha")
    evs = await run.settle(sid)
    got = [e.get("template") or e.get("state") for e in caused(evs, mid)]
    run.check(
        "another customer's 'size 40 nha' does not answer c019", not states(evs, "o-c019"), f"replies {got}"
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
        before = states(evs, oid)
        mid = await run.say(sid, thread, quick_reply=qr)
        evs = await run.settle(sid)
        got = [e.get("template") or e.get("state") for e in caused(evs, mid)]
        run.check(
            f"{qr} from another customer ({thread}) is refused",
            states(evs, oid) == before and not about(evs, mid, oid),
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


async def load(run: Run, sid: str) -> None:
    """All 200 comments with gold labels: each order label with a card should get an orders-stream consequence."""
    await run.replay(sid, labeled_session())
    evs = await run.settle(sid, quiet=3.0, limit_s=120.0)
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
    finals = Counter(
        e["state"] for e in {e["order_id"]: e for e in evs if e["type"] == "order"}.values()
    )  # last event per order
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
    run.check(
        "load: no CONFIRMED and no CANCELLED without a customer reply",
        not ({"CONFIRMED", "CANCELLED"} & set(finals)),
    )
    run.invariants("load", evs)


async def shield(run: Run, sid: str) -> None:
    """The 200 raw comments without labels: the labels brain's shield on PG through the worker, no key.

    The shield's rule is digits (worker/brain/shield.py); a phone spelled out in words is the model's to catch,
    so without a key it stays visible. The CHECK holds the shield to its own rule; the MEASURE is C5 on all of
    the gold phones, spelled ones included."""
    rows = jsonl(LABELS)
    await run.replay(sid, shield_session())
    evs = await run.settle(sid, quiet=3.0, limit_s=120.0)
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


async def demo1(run: Run, sid: str) -> None:
    """demo1 without fixtures: raw comments, product_seen, speech and two customer messages, no key."""
    await run.replay(sid, demo1_session())
    evs = await run.settle(sid, quiet=3.0)
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


async def main_async(args, prefix: str) -> Run:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=10) as http:
        run = Run(http, args.base_url, args.speed)
        for name, scenario in (("orders", orders), ("load", load), ("shield", shield), ("demo1", demo1)):
            if not args.only or name in args.only:
                await scenario(run, f"{prefix}-{name}")
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
    p.add_argument("--prefix", help="session prefix (default smoke-integ-<epoch>, a test-session prefix)")
    p.add_argument("--speed", type=float, default=20.0)
    p.add_argument("--only", nargs="*", help="orders load shield demo1")
    p.add_argument("--json", help="also write the rows as JSON here")
    args = p.parse_args(argv)
    prefix = args.prefix or f"smoke-integ-{int(time.time())}"
    return report(asyncio.run(main_async(args, prefix)), prefix, args)


if __name__ == "__main__":
    raise SystemExit(main())
