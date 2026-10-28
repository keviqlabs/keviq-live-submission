"""Catalog ambiguity must abstain and open a separate unknown draft; memory store only, no model calls."""

import copy
import json
from collections import defaultdict

import pytest

from app.contracts import make_event, now_ms
from app.settings import ROOT
from worker.brain import card
from worker.runner import Runner

CATALOG = json.loads((ROOT / "data/comments/c5/catalog.json").read_text())
PAIRS = [("đen", {"SP07", "SP09"}), ("trắng", {"SP08", "SP10"})]
UNIQUE = [
    ("sneaker", "đen", "SP01"),
    ("sneaker", "trắng", "SP02"),
    ("sneaker", "xám", "SP03"),
    ("shirt", "đen", "SP04"),
    ("shirt", "trắng", "SP05"),
    ("shirt", "xanh", "SP06"),
]


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch):
    monkeypatch.setattr(card, "CATALOG", copy.deepcopy(CATALOG))
    monkeypatch.setattr(card, "SESSIONS", {})
    monkeypatch.delenv("CARDS_FROM_REPLAY", raising=False)


def collision_groups(field):
    groups = defaultdict(set)
    for item in CATALOG:
        labels = item["categories"] if field == "categories" else [item[field]]
        for label in labels:
            for color in {variant["color"] for variant in item["variants"]}:
                groups[label, color].add(item["sku"])
    return {key: skus for key, skus in groups.items() if len(skus) > 1}


def test_catalog_has_two_coarse_collisions_but_no_same_kind_colour_pair():
    assert collision_groups("kind") == {}
    assert collision_groups("categories") == {("other", color): skus for color, skus in PAIRS}


@pytest.mark.parametrize("reverse", [False, True], ids=["catalog-order", "reversed-order"])
@pytest.mark.parametrize("color,skus", PAIRS, ids=["black-controller-keyboard", "white-controller-keyboard"])
def test_two_coarse_candidates_abstain_regardless_of_catalog_order(monkeypatch, color, skus, reverse):
    candidates = [item for item in CATALOG if item["sku"] in skus]
    assert len(candidates) == 2 and {item["kind"] for item in candidates} == {"tay cầm", "bàn phím"}
    monkeypatch.setattr(card, "CATALOG", list(reversed(CATALOG)) if reverse else CATALOG)
    assert card.match("other", color) is None


@pytest.mark.parametrize("category", ["sneaker", "shirt", "other"])
def test_missing_colour_abstains_with_several_candidates(category):
    assert card.match(category, None) is None


@pytest.mark.parametrize("category,color,sku", UNIQUE, ids=[item[2] for item in UNIQUE])
def test_unambiguous_controls_keep_their_sku(category, color, sku):
    assert card.match(category, color)["sku"] == sku


def test_a_second_synthetic_same_kind_colour_item_also_abstains(monkeypatch):
    first = copy.deepcopy(next(item for item in CATALOG if item["sku"] == "SP01"))
    second = {**copy.deepcopy(first), "sku": "SYNTH-SHOE-02", "name": "Synthetic second black shoe"}
    monkeypatch.setattr(card, "CATALOG", [first, second])
    assert card.match("sneaker", "đen") is None


class NoModel(Runner):
    def client_for(self, event):
        raise AssertionError("Model API access is forbidden in this acceptance test")


def seen(sid, event_id, color, ts):
    return make_event(
        "product_seen", sid, ts, "sim", event_id=event_id, category="other",
        attributes={"color": color, "size": None, "material": None}, confidence=0.99,
        is_new=True, frame_ts_ms=ts, caused_by=[f"frame-{event_id}"],
    )  # fmt: skip


async def ingest(client, runner, event):
    response = await client.post(f"/api/sessions/{event['session_id']}/events", json=[event])
    assert response.status_code == 200, response.text
    await runner.handle(event)
    assert runner.failures == 0


@pytest.mark.parametrize("prior_sku", ["SP07", "SP09", "SP08", "SP10"])
async def test_ambiguous_new_item_does_not_reuse_a_priced_known_card(mem_app, monkeypatch, prior_sku):
    _, store, client = mem_app
    runner = NoModel("http://test", card.HANDLERS, http=client)
    sid, ts = f"sku-ambiguity-{prior_sku}", now_ms()
    prior = next(item for item in CATALOG if item["sku"] == prior_sku)
    color = prior["variants"][0]["color"]

    # Bootstrap a known prior card with a one-item synthetic fixture, then restore the entire C5 catalog
    # before the observation under test. This is setup, not evidence that vision identified the prior SKU.
    monkeypatch.setattr(card, "CATALOG", [prior])
    await ingest(client, runner, seen(sid, "ps-prior", color, ts))
    [draft] = [event for event in await store.read(sid, 0, 100) if event["type"] == "card"]
    assert draft["sku"] == prior_sku and draft["status"] == "draft"
    reply = await client.post(
        f"/api/sessions/{sid}/cards/{draft['card_id']}/price",
        json={"card_version": draft["version"], "price": prior["price"]},
    )
    assert reply.status_code == 200 and reply.json()["changed"] is True
    before = [event for event in await store.read(sid, 0, 100) if event["type"] == "card"]
    await runner.handle(before[-1])  # follow the actual seller-price event in worker memory
    assert before[-1]["status"] == "edited" and before[-1]["price"] == prior["price"]

    monkeypatch.setattr(card, "CATALOG", CATALOG)
    assert card.match("other", color) is None
    trigger = seen(sid, "ps-next", color, ts + 3000)
    await ingest(client, runner, trigger)
    events = await store.read(sid, 0, 100)
    prior_after = [
        event for event in events if event["type"] == "card" and event["card_id"] == draft["card_id"]
    ]
    assert prior_after == before  # no extra version, price change or retirement of the seller-priced card
    [opened] = [
        event
        for event in events
        if event["type"] == "card" and event.get("caused_by") == [trigger["event_id"]]
    ]
    assert opened["card_id"] != draft["card_id"] and opened["version"] == 1
    assert opened["sku"] is None and opened["price"] is None and opened["status"] == "draft"
    assert opened["variants"] == [] and opened["stock"] == 0
    assert card.SESSIONS[sid].current == opened["card_id"]
    assert not [
        event for event in events if event["type"] in {"model_call", "order", "price_proposal", "message_out"}
    ]
    print(
        "SKU_AMBIGUITY_EVIDENCE "
        + json.dumps(
            {
                "case": prior_sku,
                "observation": {"category": "other", "color": color, "is_new": True},
                "prior_card_id": draft["card_id"],
                "prior_versions_unchanged": len(prior_after),
                "new_card_id": opened["card_id"],
                "new_sku": opened["sku"],
                "new_price": opened["price"],
                "new_status": opened["status"],
                "model_call_events": 0,
            },
            ensure_ascii=True,
        )
    )
