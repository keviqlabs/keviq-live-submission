"""C5 dry run, 0 USD: the app in this process on keviq_test, MODEL_API_KEY empty, the worker's brain and orders
handlers (card, labels, orders) fed through a real Runner.

Stand-ins, and what they cost the measure:
- media and vision: the stored 6b/minimal/1 fps rows of test1 (the spark rule's new rows) are posted as
  product_seen when their inference ended, on the loop's clock;
- voice: the test1 lines (probe/voice.json) are posted as final speech 5 ms after they end; the price reader is
  the rule reader (no key);
- the model of the labels brain: GoldClient answers each comment of a batch with its gold label, so the labels
  brain, its prompt context and its order drafts run for real but the labels are ideal (no model_call);
- the restart between loops: the worker's state goes (card sessions, labeler, runner, messenger) in one process.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace

from measure import c5

VISION = c5.ROOT / "docs/evidence/2026-10-27/vision-test1-6b-minimal-1027-1950.json"
VOICE = c5.ROOT / "data/local/test1/probe/voice.json"
SPEECH_LAG_MS = 5
SHOP_DELAY_S = 4.0


def setup_env(catalog: Path) -> tuple[str, list]:
    """Before any worker module is imported: card.py reads CATALOG_PATH at import. Returns the test DB url and
    the catalog."""
    os.environ |= {"MODEL_API_KEY": "", "CARDS_FROM_REPLAY": "1", "CATALOG_PATH": str(catalog),
                   "MODEL_SESSION_CAP_USD": "8", "MODEL_SESSION_WARN_USD": "6"}  # fmt: skip
    from app import settings

    settings.load_dotenv(c5.ROOT / ".env")
    url = settings.testing_database_url()
    if not settings.is_test_database(url):
        raise SystemExit("refusing: not a _test database")
    os.environ["DATABASE_URL"] = url
    os.environ["MODEL_API_KEY"] = ""
    return url, json.loads(catalog.read_text())


def gold_item(row: dict) -> dict:
    """The labels brain's model item for a comment, from its gold row."""
    o = row.get("order") or {}
    order = row["label"] == "order" and bool(o)
    kind = (o.get("reference_kind") or "unresolved") if order else "none"
    return {"comment_id": row["comment_id"], "label": row["label"], "reference_kind": kind,
            "sku": o.get("product") if kind == "explicit_sku" else None,
            "size": o.get("size") if order else None, "color": o.get("color") if order else None,
            "qty": o.get("qty") if order else None,
            "contact": "phone_number" if row["has_phone"] else "link" if row["has_link"] else "none"}  # fmt: skip


class GoldClient:
    """Stands in for the model client of the labels brain: no call, no cost, no model_call event."""

    def __init__(self, rows: list[dict]):
        self.rows = {r["comment_id"]: r for r in rows}
        self.batches: list[int] = []

    async def responses(self, prompt, schema, purpose=None, reasoning=None):
        from app.contracts import now_ms

        ids = [c["comment_id"] for c in json.loads(prompt[-1]["content"])["comments"]]
        self.batches.append(len(ids))
        now = now_ms()
        items = [gold_item(self.rows[i]) for i in ids if i in self.rows]
        return SimpleNamespace(data={"items": items}, infer_start_ms=now, infer_end_ms=now)


def media_items(sid: str, k: int, t0: int) -> list[tuple[int, dict]]:
    """(post at, event) for the 6b new rows and the test1 lines of loop k."""
    from app.contracts import make_event
    from worker.vision.spark import MIN_CONFIDENCE

    run = json.loads(VISION.read_text())
    last, rows = None, []
    for r in run["rows"]:  # spark.see_product's rule; must give the rows the stored run marked is_new
        if (
            r.get("error")
            or not r["has_product"]
            or r["category"] == "none"
            or r["confidence"] < MIN_CONFIDENCE
        ):
            continue
        color = r["color"].strip().lower() if isinstance(r["color"], str) and r["color"].strip() else None
        if last is None or r["category"] != last[0] or bool(color and last[1] and color != last[1]):
            rows.append(r)
            last = (r["category"], color)
        elif color and not last[1]:
            last = (r["category"], color)
    assert [r["t"] for r in rows] == [r["t"] for r in run["rows"] if r.get("is_new")], "spark rule != is_new"
    out = []
    for n, r in enumerate(rows):
        ts, end = t0 + r["t"] * 1000, t0 + r["t"] * 1000 + r["ms"]
        color = r["color"].strip().lower() if isinstance(r["color"], str) and r["color"].strip() else None
        timing = {"received_ms": ts, "infer_start_ms": ts, "infer_end_ms": end, "emitted_ms": end}
        out.append((end, make_event("product_seen", sid, ts, "replay", event_id=f"{sid}.ps-{k}-{n:02d}",
                                    timing=timing, category=r["category"], confidence=r["confidence"],
                                    attributes={"color": color, "size": None, "material": None},
                                    is_new=True, frame_ts_ms=ts)))  # fmt: skip
    for u, line in enumerate(json.loads(VOICE.read_text())["rows"]):
        start, end = t0 + round(line["start_s"] * 1000), t0 + round(line["end_s"] * 1000)
        timing = {"received_ms": end, "infer_start_ms": end, "infer_end_ms": end, "emitted_ms": end}
        out.append((end + SPEECH_LAG_MS, make_event("speech", sid, start, "replay", event_id=f"{sid}.u{k}-{u:02d}",
                                                    timing=timing, text=line["text"], final=True, lang="vi",
                                                    start_ms=start, end_ms=end)))  # fmt: skip
    return out


class WaitLines(logging.Handler):
    """The card brain's `price wait:` lines of the current loop."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record):
        msg = record.getMessage()
        if msg.startswith("price wait:"):
            self.lines.append(msg)


class Pump:
    """The worker's loop for one session: every new event, in seq order, to the actors and the runner."""

    def __init__(self, store, sid, feed, shop, runner):
        self.store, self.sid, self.feed, self.shop, self.runner = store, sid, feed, shop, runner
        self.busy, self.stop = False, asyncio.Event()

    async def run(self) -> None:
        while True:
            batch = await self.store.read(self.sid, self.feed.cursor, 500)
            if not batch:
                if self.stop.is_set():
                    return
                await asyncio.sleep(0.05)
                continue
            self.busy = True
            try:
                for ev in batch:
                    self.feed.add(ev["seq"], ev)
                    self.shop.see(ev)
                    await self.runner.handle(ev)
            finally:
                self.busy = False


async def settle(pump: Pump, tasks: list[set], timeout_s: float = 120.0) -> bool:
    """Nothing unread, nothing in hand, no task running: twice, 0.5 s apart."""
    end, calm = time.monotonic() + timeout_s, 0
    while calm < 2 and time.monotonic() < end:
        head = await pump.store.read(pump.sid, pump.feed.cursor, 1)
        idle = not head and not pump.busy and all(t.done() for s in tasks for t in s)
        calm = calm + 1 if idle else 0
        await asyncio.sleep(0.5)
    return calm >= 2


async def post_all(client, sid: str, items: list[tuple[int, dict]]) -> None:
    from app.contracts import now_ms

    for due, ev in sorted(items, key=lambda x: x[0]):
        await asyncio.sleep(max(due - now_ms(), 0) / 1000)
        r = await client.post(f"/api/sessions/{sid}/events", json=[ev])
        assert r.status_code == 200, (r.status_code, ev["type"], ev["event_id"])


async def read_all(store, sid: str) -> list[dict]:
    out: list[dict] = []
    while batch := await store.read(sid, out[-1]["seq"] if out else 0, 500):
        out += batch
    return out


async def main(run: str, loops: int, catalog: Path, out: Path, check_code: bool) -> int:
    url, want = setup_env(catalog)
    import httpx

    from app.contracts import now_ms
    from app.main import create_app
    from app.migrate import ensure_database, migrate
    from app.store import PgStore
    from connectors.sim.messenger import SimMessenger
    from measure.c5_shop import Customers, Feed, Gold, Loops, Shop
    from orders import handlers as orders
    from worker.brain import card, labels
    from worker.runner import Runner

    assert card.CATALOG == want and labels.load_catalog() == want, "worker did not read CATALOG_PATH"
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger().handlers[0].setLevel(logging.WARNING)
    waits = WaitLines()
    logging.getLogger("worker.card").setLevel(logging.INFO)
    logging.getLogger("worker.card").addHandler(waits)

    ensure_database(url)
    migrate(url)
    store = PgStore(url)
    await store.open()
    app = create_app(store)
    app.state.store = store
    gold = Gold.load(c5.C5, c5.LABELS)
    loop_ms = gold.windows["loop_ms"]
    rows_by_loop, msgs_by_loop, loop_rows, clients = {}, {}, [], []
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert await store.read(run, 0, 1) == [], f"{run} is not empty"
            cost = (await client.get("/api/cost", params={"session": run})).json()
            assert (cost["session_cap_usd"], cost["session_warn_usd"]) == (8.0, 6.0), cost
            feed, clock = Feed(), Loops()
            shop = Shop(client, run, gold, feed, clock, delay_s=SHOP_DELAY_S)
            customers = Customers(client, run, feed)
            for k in range(1, loops + 1):
                rows, msgs = c5.streams(k)
                rows_by_loop[k], msgs_by_loop[k] = rows, msgs
                gold_client = GoldClient(rows)
                clients.append(gold_client)
                labeler = labels.Labeler(client_for=lambda ctx, g=gold_client: g, catalog=want)
                handlers: dict[str, list] = {}
                for module_handlers in (card.HANDLERS, labeler.handlers(), orders.HANDLERS):
                    for etype, fns in module_handlers.items():
                        handlers.setdefault(etype, []).extend(fns)
                orders.MESSENGER = SimMessenger()
                runner = Runner("http://test", handlers, http=client)
                pump = Pump(store, run, feed, shop, runner)
                pumping = asyncio.create_task(pump.run())
                t0 = now_ms() + 1000
                clock.t0.append(t0)
                waits.lines = []
                print(f"loop {k}: t0 {t0}", flush=True)
                customers.start(msgs, t0, t0 + loop_ms + c5.TAIL_MS, k)
                await post_all(client, run, media_items(run, k, t0) + c5.comment_schedule(rows, run, t0))
                await asyncio.sleep(max(t0 + loop_ms + c5.TAIL_MS - now_ms(), 0) / 1000)
                session = lambda: card.SESSIONS.get(run)  # noqa: E731
                busy = [labeler.tasks, shop.tasks, customers.tasks, session().tasks if session() else set()]
                calm = await settle(pump, busy)
                snapshot = dict(session().waits) if session() else {}
                retired = c5.retire_events(feed, run)
                r = await client.post(f"/api/sessions/{run}/events", json=retired)
                assert r.status_code == 200, r.status_code
                calm = await settle(pump, busy) and calm
                pump.stop.set()
                await pumping
                card.SESSIONS.pop(run, None)
                loop_rows.append({"k": k, "t0_ms": t0, "end_ms": now_ms(), "settled": calm,
                                  "wait_lines": list(waits.lines), "waits": snapshot, "retired": len(retired),
                                  "runner_failures": runner.failures, "label_batches": gold_client.batches})  # fmt: skip
                print(f"loop {k}: done, retired {len(retired)}, waits {snapshot}, failures {runner.failures}")
            events = await read_all(store, run)
    finally:
        await store.close()
    run_file = {
        "run": run, "session_id": run, "sha": c5.git_sha(), "loops": loop_rows,
        "config": {"mode": "dry", "catalog_path": c5.repo_path(catalog), "catalog_sha256": c5.sha256(catalog),
                   "vision": f"stored rows {VISION.name} (6b, minimal, 1 fps)", "effort": "minimal", "fps": 1,
                   "card_line": False, "labels": "gold labels through the labels brain (GoldClient)",
                   "speech": "test1 lines, rule price reader (no key)", "cap_usd": 8.0, "warn_usd": 6.0,
                   "restart": "worker state dropped in one process", "shop_delay_s": SHOP_DELAY_S,
                   "tail_ms": c5.TAIL_MS, "loop_ms": loop_ms},
        "customers": customers.log, "shop": shop.log,
    }  # fmt: skip
    c5.finish(events, run_file, rows_by_loop, msgs_by_loop, out, url, check_code)
    return 0
