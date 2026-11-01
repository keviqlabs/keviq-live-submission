"""Optional catalog kind evidence: no model, database or production permission changes."""

import copy
from types import SimpleNamespace

import pytest

from app.contracts import make_event
from app.current import current_at
from worker.brain import card
from worker.runner import Runner
from worker.vision import frames, spark

SID = "kind-evidence"


def item(sku, kind, color):
    return {
        "sku": sku,
        "name": f"Sample {sku}",
        "kind": kind,
        "category": "other",
        "price": 21234567,
        "currency": "VND",
        "variants": [{"size": None, "color": color, "stock": 3}],
    }


CATALOG = [
    item("P-A", "tay cầm", "đen"),
    item("P-B", "bàn phím", "đen"),
    item("P-C", "tay cầm", "trắng"),
    item("P-D", "bàn phím", "trắng"),
]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(card, "CATALOG", copy.deepcopy(CATALOG))
    monkeypatch.setattr(card, "SESSIONS", {})
    monkeypatch.setattr(spark, "LAST", {})
    monkeypatch.setattr(frames, "load", lambda event: b"fake-jpeg")
    monkeypatch.setattr(frames, "put", lambda *args: None)


def reading(kind="tay cầm", color="đen", state="one_product", confidence=0.99, category="other"):
    return {
        "has_product": True,
        "category": category,
        "color": color,
        "size": None,
        "material": None,
        "kind": kind,
        "identity_state": state,
        "confidence": confidence,
    }


async def vision(monkeypatch, values):
    replies = iter(values)

    async def read(*args):
        return SimpleNamespace(data=next(replies), infer_start_ms=1, infer_end_ms=2)

    monkeypatch.setattr(spark, "read_frame", read)
    ctx = SimpleNamespace(client=lambda: object())
    out = []
    for i in range(len(values)):
        out.append(
            await spark.see_product(
                {"session_id": SID, "event_id": f"frame-{i}", "ts_ms": (i + 1) * 1000}, ctx
            )
        )
    return out


async def test_same_colour_kind_switch_is_emitted_and_repeat_is_suppressed(monkeypatch):
    out = await vision(monkeypatch, [reading(), reading(), reading("bàn phím")])
    assert [bool(x) for x in out] == [True, False, True]
    assert out[0][0]["category"] == out[2][0]["category"] == "other"
    assert [out[i][0]["attributes"]["kind"] for i in (0, 2)] == ["tay cầm", "bàn phím"]


async def test_unclear_and_low_confidence_are_explicit_changes_not_old_identity(monkeypatch):
    out = await vision(
        monkeypatch, [reading(), reading(state="unclear"), reading(), reading(confidence=0.1), reading()]
    )
    assert all(out)
    assert [o[0]["attributes"]["identity_state"] for o in out] == [
        "one_product",
        "unclear",
        "one_product",
        "unclear",
        "one_product",
    ]


async def test_prompt_and_internal_schema_only_take_kind_and_colour_not_catalog_price():
    class Client:
        async def responses(self, inputs, schema, **kwargs):
            self.inputs, self.schema, self.kwargs = inputs, schema, kwargs

    client = Client()
    await spark.read_frame(client, b"fake", None)
    text = client.inputs[0]["content"][0]["text"]
    assert "tay cầm" in text and "bàn phím" in text
    assert "21234567" not in text and "P-A" not in text
    assert set(client.schema["properties"]["kind"]["enum"]) == {"tay cầm", "bàn phím", None}
    assert client.kwargs["reasoning"] == "minimal"
    assert "sku" not in client.schema["properties"] and "price" not in client.schema["properties"]


class NoModel(Runner):
    def client_for(self, event):
        raise AssertionError("model calls forbidden")


def seen(n, **kwargs):
    value = reading(**kwargs)
    return make_event(
        "product_seen",
        SID,
        n * 1000,
        "sim",
        event_id=f"ps-{n}",
        category=value["category"],
        attributes={k: value[k] for k in ("color", "size", "material", "kind", "identity_state")},
        confidence=value["confidence"],
        is_new=True,
        frame_ts_ms=n * 1000,
        caused_by=[f"frame-{n}"],
    )


async def ingest(mem_app, event):
    _, store, client = mem_app
    assert (await client.post(f"/api/sessions/{SID}/events", json=[event])).status_code == 200
    runner = NoModel("http://test", card.HANDLERS, http=client)
    await runner.handle(event)
    assert not runner.failures
    return [e for e in await store.read(SID, 0, 1000) if e["type"] == "card"]


async def test_unique_kind_colour_gets_only_new_identity_and_catalog_variants(mem_app):
    cards = await ingest(mem_app, seen(1))
    assert len(cards) == 1
    assert cards[0]["sku"] == "P-A" and cards[0]["price"] is None and cards[0]["status"] == "draft"
    assert cards[0]["variants"] == CATALOG[0]["variants"] and cards[0]["stock"] == 3
    cards = await ingest(mem_app, seen(2, kind="bàn phím"))
    current, previous = current_at(cards, 2500)
    assert current["sku"] == "P-B" and current["price"] is None
    assert previous["sku"] == "P-A" and current["card_id"] != previous["card_id"]


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": None},
        {"kind": "unknown"},
        {"color": None},
        {"color": "xanh"},
        {"state": "unclear"},
        {"confidence": 0.1},
    ],
)
async def test_unresolved_observation_cannot_inherit_previous_known_price(mem_app, changes):
    _, store, client = mem_app
    [first] = await ingest(mem_app, seen(1))
    assert first["sku"] == "P-A"
    result = await client.post(
        f"/api/sessions/{SID}/cards/{first['card_id']}/price", json={"card_version": 1, "price": 450000}
    )
    assert result.status_code == 200
    priced = [e for e in await store.read(SID, 0, 100) if e["type"] == "card"][-1]
    runner = NoModel("http://test", card.HANDLERS, http=client)
    await runner.handle(priced)
    cards = await ingest(mem_app, seen(2, **changes))
    current, previous = current_at(cards, 2500)
    assert current["card_id"] != first["card_id"] and current["sku"] is None
    assert current["price"] is None and current["variants"] == [] and current["stock"] == 0
    assert current["image_url"] is None and current["status"] == "draft"
    assert previous["sku"] == "P-A" and previous["price"] == 450000
    cards = await ingest(mem_app, seen(3))
    current, previous = current_at(cards, 3500)
    assert current["sku"] == "P-A" and current["card_id"] != first["card_id"]
    assert current["price"] is None and previous["sku"] is None
    fresh_id = current["card_id"]
    cards = await ingest(mem_app, seen(4))
    current, _ = current_at(cards, 4500)
    assert current["card_id"] == fresh_id and current["price"] is None
    assert not [
        e
        for e in await store.read(SID, 0, 1000)
        if e["type"] in {"order", "message_out", "price_proposal", "model_call"}
    ]


async def test_multiple_catalog_candidates_remain_unresolved_even_with_kind(mem_app, monkeypatch):
    monkeypatch.setattr(card, "CATALOG", [CATALOG[0], {**CATALOG[0], "sku": "P-E"}])
    [first] = await ingest(mem_app, seen(1))
    assert first["sku"] is None and first["price"] is None


async def test_missing_kind_on_old_event_does_not_pick_single_other_candidate(mem_app, monkeypatch):
    monkeypatch.setattr(card, "CATALOG", [CATALOG[0]])
    event = seen(1)
    del event["attributes"]["kind"], event["attributes"]["identity_state"]
    [first] = await ingest(mem_app, event)
    assert first["sku"] is None and first["price"] is None


async def test_repeated_unknown_kind_preserves_version_and_cause_without_merging_different_kind(mem_app):
    cards = await ingest(mem_app, seen(1, color="xanh"))
    cid = cards[-1]["card_id"]
    cards = await ingest(mem_app, seen(2, color="xanh"))
    assert cards[-1]["card_id"] == cid and cards[-1]["version"] == 2
    assert cards[-1]["caused_by"] == ["ps-2"]
    cards = await ingest(mem_app, seen(3, kind="bàn phím", color="xanh"))
    assert cards[-1]["card_id"] != cid and cards[-1]["sku"] is None
    before = copy.deepcopy(cards)
    await ingest(mem_app, seen(2, kind="bàn phím", color="xanh"))
    _, store, _ = mem_app
    assert [e for e in await store.read(SID, 0, 1000) if e["type"] == "card"] == before


async def test_old_and_new_attributes_both_validate_without_contract_changes(mem_app):
    old = seen(1)
    del old["attributes"]["kind"], old["attributes"]["identity_state"]
    cards = await ingest(mem_app, old)
    assert cards[-1]["sku"] is None
    cards = await ingest(mem_app, seen(2))
    assert cards[-1]["sku"] == "P-A" and cards[-1]["version"] == 1
    assert cards[-1]["card_id"] != cards[0]["card_id"]
