"""Optional catalog kind evidence: no model, database or production permission changes."""

import asyncio
import copy
from types import SimpleNamespace

import pytest

from app.contracts import make_event
from app.current import current_at
from worker.brain import card, window
from worker.runner import Runner
from worker.vision import frames, spark
from worker.voice import vn

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


async def test_missing_both_keys_on_old_event_preserves_unique_coarse_match(mem_app, monkeypatch):
    monkeypatch.setattr(card, "CATALOG", [CATALOG[0]])
    event = seen(1)
    del event["attributes"]["kind"], event["attributes"]["identity_state"]
    [first] = await ingest(mem_app, event)
    assert first["sku"] == "P-A" and first["price"] is None


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


@pytest.mark.parametrize(
    "attributes",
    [
        {"kind": None},
        {"identity_state": None},
        {"kind": None, "identity_state": "unclear"},
        {"kind": "unknown", "identity_state": "one_product"},
    ],
)
async def test_present_optional_key_never_takes_unique_legacy_fallback(mem_app, monkeypatch, attributes):
    monkeypatch.setattr(card, "CATALOG", [CATALOG[0]])
    event = seen(1)
    event["attributes"] = {"color": "đen", **attributes}
    [first] = await ingest(mem_app, event)
    assert first["sku"] is None and first["price"] is None


@pytest.fixture
def speech_stub(monkeypatch):
    async def read(text, ctx):
        return vn.parse(text, set(card.COLORS)), (0, 0)

    monkeypatch.setattr(card, "read_speech", read)
    monkeypatch.setattr(window, "HOLD_MS", 0)


async def deliver_speech(mem_app, text, start, end, drain=True):
    _, store, client = mem_app
    event = make_event(
        "speech",
        SID,
        end,
        "sim",
        event_id="kind-price",
        text=text,
        start_ms=start,
        end_ms=end,
        final=True,
        lang="vi",
    )
    assert (await client.post(f"/api/sessions/{SID}/events", json=[event])).status_code == 200
    runner = NoModel("http://test", card.HANDLERS, http=client)
    await runner.handle(event)
    if drain:
        while card.SESSIONS[SID].tasks:
            await asyncio.gather(*list(card.SESSIONS[SID].tasks))
    return {e["card_id"]: e for e in await store.read(SID, 0, 1000) if e["type"] == "card"}


@pytest.mark.parametrize("interval", [(1100, 1500), (1500, 2500), (2100, 2500)])
async def test_prior_kind_price_after_switch_never_prices_current_kind(mem_app, speech_stub, interval):
    await ingest(mem_app, seen(1))
    await ingest(mem_app, seen(2, kind="bàn phím"))
    latest = await deliver_speech(mem_app, "tay cầm này 450 nghìn", *interval)
    keyboard = next(c for c in latest.values() if c["sku"] == "P-B")
    assert keyboard["price"] is None and keyboard["status"] == "draft"


@pytest.mark.parametrize("text", ["tay cầm và bàn phím này 450 nghìn", "món này 450 nghìn"])
async def test_typed_price_requires_one_matching_named_kind(mem_app, speech_stub, text):
    await ingest(mem_app, seen(1, kind="bàn phím"))
    latest = await deliver_speech(mem_app, text, 1100, 1500)
    assert all(c["price"] is None for c in latest.values())


async def test_matching_kind_price_still_opens_current_card(mem_app, speech_stub):
    await ingest(mem_app, seen(1))
    await ingest(mem_app, seen(2, kind="bàn phím"))
    latest = await deliver_speech(mem_app, "bàn phím này 450 nghìn", 2100, 2500)
    keyboard = next(c for c in latest.values() if c["sku"] == "P-B")
    assert keyboard["price"] == 450000 and keyboard["status"] == "live"
    assert keyboard["provenance"]["speech_event_ids"] == ["kind-price"]


async def test_unclear_identity_cannot_gain_price_from_named_kind(mem_app, speech_stub):
    await ingest(mem_app, seen(1, state="unclear"))
    latest = await deliver_speech(mem_app, "tay cầm này 450 nghìn", 1100, 1500)
    assert all(c["sku"] is None and c["price"] is None for c in latest.values())


async def test_priced_previous_overlap_still_waits(mem_app, speech_stub):
    _, store, client = mem_app
    [first] = await ingest(mem_app, seen(1))
    assert (
        await client.post(
            f"/api/sessions/{SID}/cards/{first['card_id']}/price", json={"card_version": 1, "price": 300000}
        )
    ).status_code == 200
    priced = [e for e in await store.read(SID, 0, 1000) if e["type"] == "card"][-1]
    await NoModel("http://test", card.HANDLERS, http=client).handle(priced)
    await ingest(mem_app, seen(2, kind="bàn phím"))
    latest = await deliver_speech(mem_app, "tay cầm này 450 nghìn", 1500, 2500)
    assert next(c for c in latest.values() if c["sku"] == "P-B")["price"] is None
    assert next(c for c in latest.values() if c["sku"] == "P-A")["price"] == 300000
    assert card.SESSIONS[SID].waits["many"] == 1


@pytest.mark.parametrize("text", ["tay cầm này 450 nghìn", "tay cầm và bàn phím này 450 nghìn"])
async def test_pending_speech_before_switch_never_prices_new_kind(mem_app, monkeypatch, speech_stub, text):
    started, release = asyncio.Event(), asyncio.Event()

    async def read(text, ctx):
        started.set()
        await release.wait()
        return vn.parse(text, set(card.COLORS)), (0, 0)

    monkeypatch.setattr(card, "read_speech", read)
    await ingest(mem_app, seen(1))
    await deliver_speech(mem_app, text, 1100, 1500, drain=False)
    await asyncio.wait_for(started.wait(), 1)
    await ingest(mem_app, seen(2, kind="bàn phím"))
    release.set()
    while card.SESSIONS[SID].tasks:
        await asyncio.gather(*list(card.SESSIONS[SID].tasks))
    _, store, _ = mem_app
    latest = {e["card_id"]: e for e in await store.read(SID, 0, 1000) if e["type"] == "card"}
    assert next(c for c in latest.values() if c["sku"] == "P-B")["price"] is None
