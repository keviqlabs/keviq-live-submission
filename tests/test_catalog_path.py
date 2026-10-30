"""CATALOG_PATH (ruling 417, D-026): the items on sale come from it; the vision and speech prompts and the
transcriber's keywords keep the words of data/catalog.json. An item with `categories` matches by membership.

0 USD: no model call; the label prompt goes to a FakeTransport.
"""

import hashlib
import json
import os
import subprocess
import sys

import pytest

from app.contracts import make_event, now_ms
from app.settings import ROOT, catalog_path
from connectors.sim.moderator import SimModerator
from meta.client import MetaClient
from meta.meter import Meter, memory_spend
from meta.transport import FakeTransport, responses_reply
from worker.brain import card, labels, window
from worker.runner import Runner
from worker.vision import frames, spark

# sha256 of the prompts on main 86264ba, before CATALOG_PATH existed (see prompt_hashes)
VISION = "dc89718d9ed46df57b644341f7feb9753b7fb49b22e23b23c9feb35b037b06c4"
SPEECH = "bdce1738e7317fd5e2a8cd2eb07f78f5e9126f0f9af26657e3dd2465ff69c254"
KEYWORDS = "18738b735fc5e178b4ee431d524728f09d2704af968ddcffdc0d23a23c40de72"
SENTENCE = "Đôi này bốn trăm rưỡi, size 38 với 39 nha"
SID = "catalog1"
BASE_SKUS = [i["sku"] for i in json.loads((ROOT / "data" / "catalog.json").read_text())]


def item(sku, name, category, variants, categories=None):
    out = {"sku": sku, "name": name, "category": category, "price": 150000, "currency": "VND"}
    if categories:
        out["categories"] = categories
    return {**out, "variants": [{"size": s, "color": c, "stock": n} for s, c, n in variants]}


# Synthetic items; colours and the scarf category are not in data/catalog.json, so a prompt that took its words
# from this file would change its hash.
FIXTURE = [
    item("FX-01", "Áo thử một", "tshirt", [("M", "tím", 3), ("L", "tím", 1)], ["tshirt", "shirt", "sweater"]),
    item("FX-02", "Giày thử hai", "sneaker", [("40", "cam", 1), ("41", "xanh lá", 2)], ["sneaker", "loafer"]),
    item("FX-03", "Giày thử ba", "loafer", [("40", "xanh lá", 2)], ["loafer"]),
    item("FX-04", "Túi thử bốn", "bag", [(None, "cam", 4)]),
    item("FX-05", "Khăn thử năm", "scarf", [(None, "tím", 5)]),
]
FX_SKUS = [i["sku"] for i in FIXTURE]


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def prompt_hashes() -> dict:
    vision = [
        spark.frame_input(b"jpeg", None),
        spark.frame_input(b"jpeg", spark.Seen("sneaker", "trắng")),
        spark.SCHEMA,
    ]
    return {
        "vision": sha(vision),
        "speech": sha([card.speech_input(SENTENCE), card.SPEECH_SCHEMA]),
        "keywords": sha(card.keywords()),
    }


def report() -> dict:
    """What a fresh process sees: run by test_prompts_keep_their_words_whatever_catalog_path_says."""
    return {
        **prompt_hashes(),
        "card_skus": [i["sku"] for i in card.CATALOG],
        "label_skus": [i["sku"] for i in labels.load_catalog()],
        "shirt": (card.match("shirt", None) or {}).get("sku"),
    }


@pytest.fixture
def fixture_file(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(FIXTURE, ensure_ascii=False))
    return path


def test_catalog_path_is_absolute_or_from_the_root(monkeypatch, tmp_path):
    monkeypatch.delenv("CATALOG_PATH", raising=False)
    assert catalog_path() == ROOT / "data" / "catalog.json"
    monkeypatch.setenv("CATALOG_PATH", "")
    assert catalog_path() == ROOT / "data" / "catalog.json"
    monkeypatch.setenv("CATALOG_PATH", "data/comments/c5/catalog.json")
    assert catalog_path() == ROOT / "data" / "comments" / "c5" / "catalog.json"
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "c.json"))
    assert catalog_path() == tmp_path / "c.json"


@pytest.mark.parametrize("which", ["unset", "fixture"])
def test_prompts_keep_their_words_whatever_catalog_path_says(which, fixture_file):
    env = {**os.environ, "MODEL_API_KEY": "", "CATALOG_PATH": str(fixture_file) if which == "fixture" else ""}
    code = "import json, sys; sys.path.insert(0, 'tests'); import test_catalog_path as t; print(json.dumps(t.report()))"
    run = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60
    )
    assert run.returncode == 0, run.stderr[-2000:]
    seen = json.loads(run.stdout.splitlines()[-1])
    assert (seen["vision"], seen["speech"], seen["keywords"]) == (VISION, SPEECH, KEYWORDS)
    skus = FX_SKUS if which == "fixture" else BASE_SKUS
    assert seen["card_skus"] == seen["label_skus"] == skus
    assert seen["shirt"] == ("FX-01" if which == "fixture" else "AO-08")


def test_match_by_membership_then_colour_one_item_or_none(monkeypatch):
    monkeypatch.setattr(card, "CATALOG", FIXTURE)
    sku = lambda category, color=None: (card.match(category, color) or {}).get("sku")  # noqa: E731
    assert sku("tshirt") == sku("shirt") == sku("sweater") == sku("sweater", "tím") == "FX-01"
    assert sku("sneaker") == "FX-02"
    assert sku("loafer") is None  # FX-02 and FX-03
    assert sku("loafer", "cam") == "FX-02"  # the colour leaves one
    assert sku("loafer", "xanh lá") is None  # both have it
    assert sku("bag") == "FX-04" and sku("scarf") == "FX-05"  # no `categories`: its `category`, as before
    assert sku("hat") is None and sku("shirt", "đen") is None


# --- product_seen: label + colour naming one SKU opens its card; none or several keep the unnamed draft


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.delenv("CARDS_FROM_REPLAY", raising=False)
    monkeypatch.setattr(window, "HOLD_MS", 0)
    monkeypatch.setattr(card, "CATALOG", FIXTURE)
    card.SESSIONS.clear()
    frames._CACHE.clear()
    yield
    card.SESSIONS.clear()
    frames._CACHE.clear()


@pytest.mark.parametrize(
    "category, color, sku, name, stock",
    [
        ("shirt", None, "FX-01", "Áo thử một", 4),  # one SKU through `categories`
        ("loafer", "cam", "FX-02", "Giày thử hai", 1),  # two by label, one by colour
        ("hat", None, None, "Mũ", 0),  # no SKU
        ("loafer", None, None, "Giày lười", 0),  # two SKUs
        ("loafer", "xanh lá", None, "Giày lười xanh lá", 0),  # two SKUs with that colour
    ],
)
async def test_product_seen_gets_a_sku_only_when_one_item_fits(
    mem_app, fresh, category, color, sku, name, stock
):
    _, store, client = mem_app
    runner = Runner("http://test", card.HANDLERS, http=client)
    runner._meta = (FakeTransport([]), Meter(memory_spend([])))
    ts = now_ms()
    event = make_event(
        "product_seen", SID, ts, "replay", event_id="ps-a", category=category,
        attributes={"color": color, "size": None, "material": None}, confidence=0.9, is_new=True,
        frame_ts_ms=ts, caused_by=["fr-1"],
    )  # fmt: skip
    assert (await client.post(f"/api/sessions/{SID}/events", json=[event])).status_code == 200
    assert await runner.handle(event) == []
    [out] = [e for e in await store.read(SID, 0, 100) if e["type"] == "card"]
    assert (out["sku"], out["name"], out["stock"], out["price"]) == (sku, name, stock, None)
    assert bool(out["variants"]) == (sku is not None)


# --- label prompt


async def test_label_prompt_lists_the_catalog_path_items(monkeypatch, fixture_file):
    monkeypatch.setenv("CATALOG_PATH", str(fixture_file))
    transport = FakeTransport([responses_reply("not json"), responses_reply("not json")])

    async def emit(fields):
        pass

    labeler = labels.Labeler(
        client_for=lambda ctx: MetaClient(transport, Meter(memory_spend([0.0]), 30, 300, 20), emit),
        moderator=SimModerator(),
    )
    assert labeler.skus == set(FX_SKUS)
    comment = {"comment_id": "c-1", "text": "lấy cái này size M", "ts_ms": 1000}
    assert await labeler._ask([labels.Pending(comment, None, [], "none")], []) == ({}, None)
    sent = json.loads(transport.requests[0][1]["input"][1]["content"])["catalog"]
    assert [c["sku"] for c in sent] == FX_SKUS
    assert sent[0] == {"sku": "FX-01", "name": "Áo thử một", "sizes": ["L", "M"], "colors": ["tím"]}
