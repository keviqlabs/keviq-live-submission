"""worker/brain/labels + shield + measure/labels on a fake model transport (no real model call, no real hide).

The oracle transport answers each batch from the hand labels, so the end-to-end test checks the plumbing and
the timeline resolution in code (C3), not model quality: its F1 is 1.0 by construction.
"""

import asyncio
import importlib.util
import json

import pytest

from app.contracts import make_event, now_ms
from app.settings import ROOT
from connectors.sim.comments import SESSION, events
from connectors.sim.moderator import SimModerator
from connectors.sim.replay import plan
from measure import labels as measure_labels
from meta.client import MetaClient
from meta.meter import Meter, memory_spend
from meta.transport import FakeTransport, Reply, responses_reply
from worker.brain import labels as labels_mod
from worker.brain.labels import Labeler, cards_at, resolve
from worker.brain.shield import first_label, scan
from worker.runner import Runner, build

GOLD = measure_labels.load_gold()


class OracleTransport:
    """Answers label_comments from the gold rows, like a model that reads every comment right."""

    def __init__(self, wrong: set[str] = frozenset()):
        self.wrong, self.requests = wrong, []

    async def post(self, path: str, body: dict) -> Reply:
        self.requests.append(body)
        comments = json.loads(body["input"][1]["content"])["comments"]
        items = []
        for c in comments:
            g = GOLD[c["comment_id"]]
            o = g["order"] or {}
            label = "other" if c["comment_id"] in self.wrong else g["label"]
            items.append(
                {
                    "comment_id": c["comment_id"],
                    "label": label,
                    "reference_kind": o.get("reference_kind", "none"),
                    "sku": o.get("sku") if o.get("reference_kind") == "explicit_sku" else None,
                    "size": o.get("size"),
                    "color": o.get("color"),
                    "qty": o.get("qty"),
                    "contact": "phone_number" if g["has_phone"] else "none",
                }
            )
        return responses_reply(json.dumps({"items": items}, ensure_ascii=False), 900, 300)

    async def aclose(self):
        pass


def wire(client, transport, moderator=None):
    """(labeler, runner) whose model calls go to `transport` and post model_call like the real runner."""
    holder = {}

    def client_for(ctx):
        async def emit(fields):
            await holder["runner"].post([build(ctx.event, {"type": "model_call", **fields}, now_ms())])

        return MetaClient(transport, Meter(memory_spend([0.0]), 30, 300, 20), emit)

    labeler = Labeler(client_for=client_for, moderator=moderator or SimModerator())
    holder["runner"] = runner = Runner("http://test", labeler.handlers(), http=client)
    return labeler, runner


async def drain(labeler):
    """Wait for every flush the labeler spawned; a cancelled timer is fine, any other error is raised.

    A batch task can finish between its consumer's end and this task's wakeup, with its removal from `tasks`
    still a pending callback; gathering only done tasks does not yield, so yield once per round to let it run.
    """
    while labeler.tasks:
        await asyncio.sleep(0)
        for r in await asyncio.gather(*list(labeler.tasks), return_exceptions=True):
            if isinstance(r, Exception):
                raise r


async def feed(client, runner, schedule):
    for _, e in schedule:
        r = await client.post(f"/api/sessions/{e['session_id']}/events", json=[e])
        assert r.status_code == 200, r.text
        await runner.handle(e)


def cards():
    return [e for e in events() if e["type"] == "card"]


# --- shield


def test_scan_finds_phone_numbers_and_links_only():
    assert scan("chốt đôi này sđt 0900 000 012") == ["phone_number"]
    assert scan("ib +84 900 000 012 nha") == ["phone_number"]
    assert scan("nhận quà tại bit.ly/qua-tang") == ["link"]
    assert scan("vào https://shop.example/x hoặc gọi 0900.000.031") == ["phone_number", "link"]
    for text in ["giá 450000 hả shop", "1.5tr được không", "size 40 màu đen x2", "ship Đà Nẵng ok không"]:
        assert scan(text) == [], text
    assert first_label(["link"]) == "scam" and first_label(["phone_number"]) == "other"


def test_scan_agrees_with_the_hand_labels():
    """Every hand-marked phone number and link is found, including written digits, and nothing else."""
    missed, extra = [], []
    for cid, g in GOLD.items():
        got = set(scan(g["text"]))
        want = {s for s, on in (("phone_number", g["has_phone"]), ("link", g["has_link"])) if on}
        missed += [(cid, s) for s in want - got]
        extra += [(cid, s) for s in got - want]
    assert extra == []
    assert missed == []


def test_gold_is_what_the_authoring_rows_fix():
    """The committed gold equals author.py's output, labels set by each row's code; the Founder approved the first 30."""
    spec = importlib.util.spec_from_file_location("author", ROOT / "data" / "comments" / "author.py")
    author = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(author)
    assert {r["comment_id"]: r for r in author.rows()} == GOLD
    assert author.card_events() == [json.loads(x) for x in (ROOT / "data/comments/cards.jsonl").open()]
    sources = [GOLD[f"c{n:03d}"]["label_source"] for n in range(1, 201)]
    assert set(sources[:30]) == {"founder_approved"} and set(sources[30:]) == {"template"}
    actions = {cid: g["expected_action"] for cid, g in GOLD.items() if "expected_action" in g}
    assert actions == {
        "c019": "ask_customer",
        "c022": "ask_customer",
        "c034": "needs_seller",
        "c092": "needs_seller",
        "c106": "ask_customer",
        "c169": "none",
        "c192": "confirm_cancel",
    }
    assert {GOLD[cid]["label"] for cid, a in actions.items() if a in ("ask_customer", "needs_seller")} == {
        "order"
    }
    assert GOLD["c169"]["label"] == GOLD["c192"]["label"] == "other"

    # The cancel comes from a customer with exactly one earlier order, the size 39 it names;
    # the "don't cancel" comment from another customer who has an order.
    def earlier_orders(cid):
        g = GOLD[cid]
        return [
            o
            for o in GOLD.values()
            if o["user_ref"] == g["user_ref"] and o["label"] == "order" and o["ts_ms"] < g["ts_ms"]
        ]

    assert [o["order"]["size"] for o in earlier_orders("c192")] == ["39"]
    assert earlier_orders("c169") and GOLD["c169"]["user_ref"] != GOLD["c192"]["user_ref"]


# --- timeline resolution


def item(kind, sku=None, size=None, color=None, qty=1):
    return {"reference_kind": kind, "sku": sku, "size": size, "color": color, "qty": qty}


def test_previous_card_is_the_card_shown_before_the_live_one():
    current, previous = cards_at(cards(), 75_000)
    assert (current["card_id"], previous["card_id"]) == ("card-02", "card-01")
    draft = resolve(item("previous_card", size="40"), cards(), 75_000, {"GIAY-01"})
    assert draft == {
        "sku": "GIAY-01",
        "variant": {"size": "40", "color": None},
        "qty": 1,
        "referenced_card_id": "card-01",
        "reference_kind": "previous_card",
    }


def test_retired_card_and_explicit_sku():
    # card-03 is retired at 199.5 s; its last frame appearance still makes it current until card-04 appears.
    current, previous = cards_at(cards(), 199_800)
    assert (current["card_id"], previous["card_id"]) == ("card-03", "card-02")
    assert cards_at(cards(), 1_000) == (None, None)
    assert resolve(item("current_card"), cards(), 1_000, set())["reference_kind"] == "unresolved"
    # After card-04 goes live, "the one before" is the retired card-03.
    assert cards_at(cards(), 205_000)[1]["card_id"] == "card-03"
    skus = {"GIAY-01", "TUI-03", "GIAY-02"}
    shown = resolve(item("explicit_sku", sku="GIAY-01"), cards(), 300_000, skus)
    assert (shown["referenced_card_id"], shown["sku"], shown["reference_kind"]) == (
        None,
        "GIAY-01",
        "explicit_sku",
    )  # An earlier shown SKU outside the current/previous pair does not grant a target.
    not_shown = resolve(item("explicit_sku", sku="GIAY-02"), cards(), 300_000, skus)
    assert (not_shown["referenced_card_id"], not_shown["sku"], not_shown["reference_kind"]) == (
        None,
        "GIAY-02",
        "explicit_sku",
    )
    unknown = resolve(item("explicit_sku", sku="XX-99"), cards(), 300_000, skus)
    assert (unknown["referenced_card_id"], unknown["reference_kind"]) == (None, "unresolved")
    assert resolve(item("current_card", qty=0), cards(), 300_000, skus)["qty"] == 1


def test_late_comment_pins_to_the_card_live_when_it_was_written():
    late = [g for g in GOLD.values() if "arrives_ms" in g and g["order"]]
    assert late, "dataset has late orders"
    for g in late:
        assert cards_at(cards(), g["arrives_ms"])[0]["card_id"] != g["order"]["referenced_card_id"]
        draft = resolve(item(g["order"]["reference_kind"], sku=g["order"]["sku"]), cards(), g["ts_ms"], set())
        assert draft["referenced_card_id"] == g["order"]["referenced_card_id"], g["comment_id"]


def version(card_id, number, status, price, ts_ms, frame_ts_ms):
    """A card version as the store keeps it: only the fields the timeline reads."""
    return {
        "card_id": card_id,
        "version": number,
        "status": status,
        "price": price,
        "sku": f"SKU-{card_id[-1]}",
        "ts_ms": ts_ms,
        "provenance": {"frame_ts_ms": frame_ts_ms},
    }


DRAFT_THEN_LIVE = [
    version("card-A", 1, "live", 150_000, 10_000, 10_000),
    version("card-B", 1, "draft", None, 12_000, 12_000),
    version("card-B", 2, "live", 200_000, 13_000, 12_000),
]


def ident(card):
    return card and (card["card_id"], card["version"])


def test_live_snapshot_skips_an_unpriced_draft():
    # The media-time snapshot knows the draft; the live snapshot (sessions without a binding) does not.
    # (media-time identity carries each card's latest supplied version, so the prefix pins the draft version)
    current, previous = cards_at(DRAFT_THEN_LIVE, 12_500)
    assert (current["card_id"], previous["card_id"]) == ("card-B", "card-A")
    current, previous = cards_at(DRAFT_THEN_LIVE[:2], 12_500)
    assert (ident(current), ident(previous)) == (("card-B", 1), ("card-A", 1))
    # Live current is card-A; previous is the latest other card shown, the draft's first version, as on the base.
    current, previous = cards_at(DRAFT_THEN_LIVE, 12_500, live_only=True)
    assert (ident(current), ident(previous)) == (("card-A", 1), ("card-B", 1))
    current, previous = cards_at(DRAFT_THEN_LIVE, 13_500, live_only=True)
    assert (ident(current), ident(previous)) == (("card-B", 2), ("card-A", 1))
    draft = resolve(item("current_card"), DRAFT_THEN_LIVE, 12_500, set(), live_only=True)
    assert draft["referenced_card_id"] == "card-A"


# --- the stream end to end on the in-memory app


async def test_two_hundred_comments_through_the_stream(mem_app, monkeypatch):
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.01)
    _, store, client = mem_app
    moderator = SimModerator()
    transport = OracleTransport()
    labeler, runner = wire(client, transport, moderator)
    await feed(client, runner, plan(events(), SESSION, now_ms(), 1.0))
    await drain(labeler)
    out = await store.read(SESSION, 0, 5000)

    assert all(len(json.loads(r["input"][1]["content"])["comments"]) <= 20 for r in transport.requests)
    assert len(transport.requests) >= 10
    assert all(r["reasoning"] == {"effort": "minimal"} for r in transport.requests)
    calls = [e for e in out if e["type"] == "model_call"]
    assert len(calls) == len(transport.requests) and {c["purpose"] for c in calls} == {"label_comments"}

    r = measure_labels.report(out, GOLD)
    assert r["comments"] == 200
    assert r["labels"]["micro_f1"] == 1.0
    assert {s: (v["n"], v["micro_f1"]) for s, v in r["labels_by_source"].items()} == {
        "founder_approved": (30, 1.0),
        "template": (170, 1.0),
    }
    assert r["order_fields"]["all_exact"] == 1.0, r["order_fields"]["wrong"]
    assert r["expected_action"] == {
        "ask_customer": {"n": 3, "ok": 3, "misses": []},
        "needs_seller": {"n": 2, "ok": 2, "misses": []},
        "confirm_cancel": {"n": 1, "ok": 1, "misses": []},
        "none": {"n": 1, "ok": 1, "misses": []},
    }
    assert r["core"]["c3"]["accuracy"] == 1.0 and r["core"]["c3"]["n"] == 74
    sh = r["shield"]
    assert sh["scam_contact_hide_rate"] == 1.0 and sh["scam_hide_or_warn_rate"] == 1.0
    assert sh["phone_hidden_rate"] == 1.0
    # Every phone/link comment was hidden on receipt, before any model call answered.
    contact = {cid for cid, g in GOLD.items() if scan(g["text"])}
    first = {}
    for e in out:
        if e["type"] == "comment_labeled":
            first.setdefault(e["comment_id"], e)
    on_receipt = {
        cid for cid, e in first.items() if e["action"] == "hide" and "infer_start_ms" not in e["timing"]
    }
    assert on_receipt == contact
    # Written digits are shield signals too; the batch does not need to supply the first hide.
    spelled = {cid for cid, g in GOLD.items() if g["has_phone"] and not any(ch.isdigit() for ch in g["text"])}
    assert len(spelled) == 1 and spelled <= on_receipt
    assert {h["comment_id"] for h in moderator.hidden} >= contact
    # Every unresolved order asks the customer which item, caused by the comment and its label.
    labeled = {e["event_id"]: e for e in out if e["type"] == "comment_labeled"}
    asks = [e for e in out if e["type"] == "message_out"]
    unresolved = [cid for cid, g in GOLD.items() if (g["order"] or {}).get("reference_kind") == "unresolved"]
    assert sorted(labeled[a["caused_by"][1]]["comment_id"] for a in asks) == sorted(unresolved)
    assert all(a["template"] == "ask_which_item" and a["payload"]["options"] for a in asks)
    assert all(
        e.get("caused_by") and e.get("timing") for e in out if e["type"] != "comment" and e["type"] != "card"
    )


class TimedOracle(OracleTransport):
    """The oracle, `seconds` per call; when each call started and how many were open at once."""

    def __init__(self, seconds: float = 0.0):
        super().__init__()
        self.seconds, self.at, self.open, self.most = seconds, [], 0, 0

    async def post(self, path: str, body: dict) -> Reply:
        self.at.append(asyncio.get_running_loop().time())
        self.open += 1
        self.most = max(self.most, self.open)
        try:
            await asyncio.sleep(self.seconds)
            return await super().post(path, body)
        finally:
            self.open -= 1


def sizes(transport) -> list[int]:
    return [len(json.loads(r["input"][1]["content"])["comments"]) for r in transport.requests]


async def test_a_batch_goes_at_twenty_and_the_rest_after_the_wait(mem_app):
    _, store, client = mem_app
    transport = TimedOracle()
    labeler, runner = wire(client, transport)
    timed = plan(events(), SESSION, now_ms(), 1.0)
    schedule = [p for p in timed if p[1]["type"] == "card"][:1]
    comments = [p for p in timed if p[1]["type"] == "comment"][:21]
    t0 = asyncio.get_running_loop().time()
    await feed(client, runner, schedule + comments)
    await drain(labeler)
    assert sizes(transport) == [20, 1]
    assert transport.at[0] - t0 < labels_mod.BATCH_WAIT_S / 2  # twenty queued: no wait
    assert transport.at[1] - t0 >= labels_mod.BATCH_WAIT_S  # one queued: it waited for more
    ctx = json.loads(transport.requests[0]["input"][1]["content"])
    assert ctx["catalog"] and ctx["card_timeline"][0]["card_id"] == "card-01"


async def test_a_flood_opens_a_call_per_twenty_up_to_the_lane_cap(mem_app):
    """Forty-five comments at once: two full batches open at once (each cut as its twenty queue), the five left
    wait their window; never more calls open than the session's lanes (phone numbers are still hidden on
    receipt, before any batch)."""
    _, store, client = mem_app
    transport = TimedOracle(0.5)
    labeler, runner = wire(client, transport)
    timed = plan(events(), SESSION, now_ms(), 1.0)
    schedule = [p for p in timed if p[1]["type"] == "card"][:1]
    comments = [p for p in timed if p[1]["type"] == "comment"][:45]
    await feed(client, runner, schedule + comments)
    await asyncio.sleep(0.05)
    assert labeler.backlog() == {"waiting": 5, "in_flight": 40}
    await drain(labeler)
    assert sizes(transport) == [20, 20, 5]
    assert 2 <= transport.most <= labels_mod.BATCH_LANES
    assert labeler.backlog() == {"waiting": 0, "in_flight": 0}
    labeled = {e["comment_id"] for e in await store.read(SESSION, 0, 500) if e["type"] == "comment_labeled"}
    assert labeled == {e["comment_id"] for _, e in comments}


async def test_invalid_model_output_labels_the_batch_other_and_goes_on(mem_app, monkeypatch):
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.01)
    _, store, client = mem_app
    transport = FakeTransport([responses_reply("not json"), responses_reply('{"items": "x"}')])
    labeler, runner = wire(client, transport)
    schedule = plan(events(3), SESSION, now_ms(), 1.0)
    await feed(client, runner, schedule)
    await drain(labeler)
    out = await store.read(SESSION, 0, 100)
    labels = [e for e in out if e["type"] == "comment_labeled"]
    assert [e["label"] for e in labels] == ["other", "other", "other"]
    assert [c["ok"] for c in out if c["type"] == "model_call"] == [True, True]


async def test_missing_key_does_not_stop_the_stream(mem_app, monkeypatch):
    """The default client raises without MODEL_API_KEY; the batch falls back to `other` (C7)."""
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.01)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    _, store, client = mem_app
    labeler = Labeler()
    runner = Runner("http://test", labeler.handlers(), http=client)
    await feed(client, runner, plan(events(2), SESSION, now_ms(), 1.0))
    await drain(labeler)
    labels = [e for e in await store.read(SESSION, 0, 100) if e["type"] == "comment_labeled"]
    assert [e["label"] for e in labels] == ["other", "other"]


async def test_session_with_fixture_cards_is_left_to_its_fixtures(mem_app, demo_events):
    _, store, client = mem_app
    labeler, runner = wire(client, FakeTransport([]))
    for e in plan(demo_events, "demo1-t", now_ms(), 1000.0):
        await runner.handle(e[1])
    await drain(labeler)
    assert labeler.fixture_sessions == {"demo1-t"}
    assert not labeler.queues["demo1-t"]
    assert [e for e in await store.read("demo1-t", 0, 100)] == []


# --- measure/labels


def labeled(cid, label, draft=None, action="none", ts=1000):
    return {
        "event_id": f"l-{cid}",
        "type": "comment_labeled",
        "comment_id": cid,
        "label": label,
        "order_draft": draft,
        "action": action,
        "ts_ms": ts,
    }


def comment(cid, ts=0):
    return {"event_id": cid, "type": "comment", "comment_id": cid, "ts_ms": ts}


def test_measure_counts_misses_and_the_latest_label():
    gold = {cid: GOLD[cid] for cid in ["c001", "c002", "c003", "c004"]}
    evs = [comment(c) for c in gold]
    evs += [labeled("c001", "scam"), labeled("c001", gold["c001"]["label"])]  # latest label wins
    evs += [labeled("c002", gold["c002"]["label"]), labeled("c003", "spam")]  # c003 wrong, c004 unlabeled
    r = measure_labels.label_f1(evs, gold)
    assert (r["n"], r["labeled"]) == (4, 3)
    assert r["micro_precision"] == pytest.approx(2 / 3) and r["micro_recall"] == pytest.approx(2 / 4)
    assert r["micro_f1"] == pytest.approx(2 * (2 / 3) * 0.5 / (2 / 3 + 0.5))


def test_measure_splits_f1_by_label_source():
    gold = {cid: GOLD[cid] for cid in ["c001", "c002", "c031", "c032"]}
    evs = [comment(c) for c in gold] + [labeled(c, g["label"]) for c, g in gold.items() if c != "c002"]
    r = measure_labels.report(evs, gold)
    assert r["labels_by_source"]["template"] == {"n": 2, "labeled": 2, "micro_f1": 1.0}
    assert r["labels_by_source"]["founder_approved"]["n"] == 2
    assert r["labels_by_source"]["founder_approved"]["micro_f1"] == pytest.approx(2 / 3)
    assert "founder_approved n=2 F1=0.667;" in measure_labels.table(r)
    pending = {**gold, "c001": {**gold["c001"], "label_source": "founder_pending"}}
    assert "founder_pending n=1 F1=1.000 (Founder review pending" in measure_labels.table(
        measure_labels.report(evs, pending)
    )


def test_measure_expected_action_counts_guesses():
    """ask_customer misses when the model fills in what the comment left open; needs_seller needs the whole order;
    confirm_cancel and none need the label other."""

    def draft(cid, **change):
        o = {**GOLD[cid]["order"], **change}
        return {
            "sku": o["sku"],
            "variant": {"size": o["size"], "color": o["color"]},
            "qty": o["qty"],
            "referenced_card_id": o["referenced_card_id"],
            "reference_kind": o["reference_kind"],
        }

    gold = {cid: GOLD[cid] for cid in ["c019", "c022", "c106", "c034", "c092", "c031", "c169", "c192"]}
    evs = [comment(c) for c in gold]
    evs += [labeled("c019", "order", draft("c019", size="40"))]
    evs += [
        labeled("c022", "order", draft("c022", reference_kind="previous_card", referenced_card_id="card-01"))
    ]
    evs += [labeled("c106", "other"), labeled("c034", "order", draft("c034"))]
    evs += [labeled("c092", "order", draft("c092", qty=2)), labeled("c031", GOLD["c031"]["label"])]
    evs += [labeled("c169", "other"), labeled("c192", "order", draft("c032"))]
    r = measure_labels.report(evs, gold)
    assert r["expected_action"] == {
        "ask_customer": {
            "n": 3,
            "ok": 0,
            "misses": [
                {"comment_id": "c019", "why": "guessed size 40"},
                {"comment_id": "c022", "why": "guessed reference_kind previous_card"},
                {"comment_id": "c106", "why": "label other"},
            ],
        },
        "needs_seller": {"n": 2, "ok": 1, "misses": [{"comment_id": "c092", "why": "wrong qty"}]},
        "none": {"n": 1, "ok": 1, "misses": []},
        "confirm_cancel": {"n": 1, "ok": 0, "misses": [{"comment_id": "c192", "why": "label order"}]},
    }
    assert (
        "expected_action confirm_cancel: model part right 0/1 misses: c192 label order"
        in measure_labels.table(r)
    )
    assert (
        "expected_action needs_seller: model part right 1/2 misses: c092 wrong qty" in measure_labels.table(r)
    )


def test_measure_order_fields_and_shield():
    cid = next(c for c, g in GOLD.items() if g["label"] == "order" and g["order"]["size"])
    want = GOLD[cid]["order"]
    draft = {
        "sku": want["sku"],
        "variant": {"size": want["size"], "color": want["color"]},
        "qty": want["qty"] + 1,
        "referenced_card_id": want["referenced_card_id"],
        "reference_kind": want["reference_kind"],
    }
    r = measure_labels.order_fields([labeled(cid, "order", draft)], {cid: GOLD[cid]})
    assert r["fields"]["qty"] == 0.0 and r["fields"]["sku"] == 1.0 and r["all_exact"] == 0.0
    scam = next(c for c, g in GOLD.items() if g["label"] == "scam" and g["has_link"])
    sh = measure_labels.shield([comment(scam), labeled(scam, "scam", action="warn")], {scam: GOLD[scam]})
    assert sh["scam_contact_hide_rate"] == 0.0 and sh["scam_hide_or_warn_rate"] == 1.0


class FixedTransport:
    """Labels the given comment ids as an order for the current card, size 40."""

    def __init__(self, comment_ids: set[str]):
        self.comment_ids = comment_ids

    async def post(self, path: str, body: dict) -> Reply:
        comments = json.loads(body["input"][1]["content"])["comments"]
        items = [
            {
                "comment_id": c["comment_id"],
                "label": "order" if c["comment_id"] in self.comment_ids else "other",
                "reference_kind": "current_card" if c["comment_id"] in self.comment_ids else "none",
                "sku": None,
                "size": "40" if c["comment_id"] in self.comment_ids else None,
                "color": None,
                "qty": 1 if c["comment_id"] in self.comment_ids else None,
                "contact": "none",
            }
            for c in comments
        ]
        return responses_reply(json.dumps({"items": items}, ensure_ascii=False), 900, 300)

    async def aclose(self):
        pass


def card_event(sid, card_id, number, status, price, ts_ms, frame_ts_ms):
    return make_event(
        "card",
        sid,
        ts_ms,
        "sim",
        event_id=f"{sid}-{card_id}-v{number}",
        card_id=card_id,
        sku="GIAY-01",
        name="Sneaker",
        price=price,
        currency="VND",
        variants=[{"size": "40", "color": "trắng", "stock": 2}],
        stock=2,
        image_url=f"sim://{sid}/{card_id}.png",
        version=number,
        status=status,
        provenance={"frame_ts_ms": frame_ts_ms, "speech_event_ids": [], "edited_by": None},
    )


def order_comment(sid, comment_id, ts_ms):
    return make_event(
        "comment",
        sid,
        ts_ms,
        "sim",
        event_id=comment_id,
        comment_id=comment_id,
        user_ref="u-1",
        text="chốt đôi này size 40",
        reply_to=None,
    )


async def test_session_without_binding_orders_from_the_live_card_not_the_draft(mem_app, monkeypatch):
    monkeypatch.setattr(labels_mod, "BATCH_WAIT_S", 0.01)
    _, store, client = mem_app
    sid = "nobinding1"
    labeler, runner = wire(client, FixedTransport({"nb-1", "nb-2"}))
    assert await runner.attachment_enabled(sid) is False
    first = [card_event(sid, "card-A", 1, "live", 150_000, 10_000, 10_000)]
    first += [card_event(sid, "card-B", 1, "draft", None, 12_000, 12_000), order_comment(sid, "nb-1", 12_500)]
    await feed(client, runner, [(0, e) for e in first])
    await drain(labeler)
    later = [
        card_event(sid, "card-B", 2, "live", 200_000, 13_000, 12_000),
        order_comment(sid, "nb-2", 13_500),
    ]
    await feed(client, runner, [(0, e) for e in later])
    await drain(labeler)
    labeled = {e["comment_id"]: e for e in await store.read(sid, 0, 100) if e["type"] == "comment_labeled"}
    draft = labeled["nb-1"]["order_draft"]
    assert (draft["referenced_card_id"], draft["reference_kind"]) == ("card-A", "current_card")
    draft = labeled["nb-2"]["order_draft"]
    assert (draft["referenced_card_id"], draft["reference_kind"]) == ("card-B", "current_card")
