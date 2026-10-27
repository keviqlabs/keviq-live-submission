"""worker/brain/labels + shield + measure/labels on a fake model transport (no real model call, no real hide).

The oracle transport answers each batch from the hand labels, so the end-to-end test checks the plumbing and
the timeline resolution in code (C3), not model quality: its F1 is 1.0 by construction.
"""

import asyncio
import importlib.util
import json

import pytest

from app.contracts import now_ms
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
                    "scam_signals": ["phone_number"] if g["has_phone"] else [],
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
    """Wait for every flush the labeler spawned; a cancelled timer is fine, any other error is raised."""
    while labeler.tasks:
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
    """Every hand-marked phone number and link is found, nothing else; only the spelled-out number is missed."""
    missed, extra = [], []
    for cid, g in GOLD.items():
        got = set(scan(g["text"]))
        want = {s for s, on in (("phone_number", g["has_phone"]), ("link", g["has_link"])) if on}
        missed += [(cid, s) for s in want - got]
        extra += [(cid, s) for s in got - want]
    assert extra == []
    assert len(missed) == 1 and missed[0][1] == "phone_number"
    assert any(ch.isdigit() for ch in GOLD[missed[0][0]]["text"]) is False


def test_gold_is_what_the_authoring_rows_fix():
    """The committed gold equals author.py's output, labels set by each row's code; the first 30 await the Founder."""
    spec = importlib.util.spec_from_file_location("author", ROOT / "data" / "comments" / "author.py")
    author = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(author)
    assert {r["comment_id"]: r for r in author.rows()} == GOLD
    assert author.card_events() == [json.loads(x) for x in (ROOT / "data/comments/cards.jsonl").open()]
    sources = [GOLD[f"c{n:03d}"]["label_source"] for n in range(1, 201)]
    assert set(sources[:30]) <= {"founder_pending", "founder_approved"} and set(sources[30:]) == {"template"}


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
    # card-03 is retired at 199.5 s, card-04 goes live at 200 s. In between the newest live card is card-02
    # (earlier cards stay live in the store); "the one just now" is still the retired card-03.
    current, previous = cards_at(cards(), 199_800)
    assert (current["card_id"], previous["card_id"]) == ("card-02", "card-03")
    assert cards_at(cards(), 1_000) == (None, None)
    assert resolve(item("current_card"), cards(), 1_000, set())["reference_kind"] == "unresolved"
    # After card-04 goes live, "the one before" is the retired card-03.
    assert cards_at(cards(), 205_000)[1]["card_id"] == "card-03"
    skus = {"GIAY-01", "TUI-03", "GIAY-02"}
    shown = resolve(item("explicit_sku", sku="GIAY-01"), cards(), 300_000, skus)
    assert (shown["referenced_card_id"], shown["sku"]) == ("card-01", "GIAY-01")
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
    calls = [e for e in out if e["type"] == "model_call"]
    assert len(calls) == len(transport.requests) and {c["purpose"] for c in calls} == {"label_comments"}

    r = measure_labels.report(out, GOLD)
    assert r["comments"] == 200
    assert r["labels"]["micro_f1"] == 1.0
    assert {s: (v["n"], v["micro_f1"]) for s, v in r["labels_by_source"].items()} == {
        "founder_pending": (30, 1.0),
        "template": (170, 1.0),
    }
    assert r["order_fields"]["all_exact"] == 1.0, r["order_fields"]["wrong"]
    assert r["core"]["c3"]["accuracy"] == 1.0 and r["core"]["c3"]["n"] == 71
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
    # The number spelled out in words has no digits for the regex; the batch hides it on the model's signal.
    spelled = {cid for cid, g in GOLD.items() if g["has_phone"]} - contact
    assert len(spelled) == 1 and all(first[cid]["action"] == "hide" for cid in spelled)
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


async def test_batch_flushes_at_twenty_then_on_the_timer(mem_app):
    _, store, client = mem_app
    transport = OracleTransport()
    labeler, runner = wire(client, transport)
    schedule = [p for p in plan(events(), SESSION, now_ms(), 1.0) if p[1]["type"] == "card"][:1]
    comments = [p for p in plan(events(), SESSION, now_ms(), 1.0) if p[1]["type"] == "comment"][:21]
    await feed(client, runner, schedule + comments)
    assert len(labeler.queues[SESSION]) == 1 and SESSION in labeler.timers
    pending = [
        t for t in labeler.tasks if t is not labeler.timers[SESSION]
    ]  # the batch of 20, the cancelled timer
    assert not [r for r in await asyncio.gather(*pending, return_exceptions=True) if isinstance(r, Exception)]
    assert [len(json.loads(r["input"][1]["content"])["comments"]) for r in transport.requests] == [20]
    await drain(labeler)
    assert [len(json.loads(r["input"][1]["content"])["comments"]) for r in transport.requests] == [20, 1]
    ctx = json.loads(transport.requests[0]["input"][1]["content"])
    assert ctx["catalog"] and ctx["card_timeline"][0]["card_id"] == "card-01"


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
    assert r["labels_by_source"]["founder_pending"]["n"] == 2
    assert r["labels_by_source"]["founder_pending"]["micro_f1"] == pytest.approx(2 / 3)
    assert "founder_pending n=2 F1=0.667 (Founder review pending" in measure_labels.table(r)


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
