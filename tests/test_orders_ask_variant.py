"""An order comment on a card with several variants that does not say which ("lấy đôi này") asks the same customer
to choose the missing size/color (ask_variant) instead of drafting a guess. The customer's pick, or text naming
one variant of the card version the comment was priced from, makes the usual DRAFT -> rules -> confirm_order;
anything else asks again. No order is made before the pick and none is ever CONFIRMED by us."""

import json

import pytest

from app.contracts import now_ms
from app.settings import ROOT
from connectors.sim import replay
from connectors.sim.messenger import parse_webhook, webhook_body
from measure.core import c1
from orders import flow, handlers
from orders.intent import customer_reply
from worker.runner import Runner

SID = "t1"
START = 300_000  # the scene starts 5 min ago, so c106 (ts 285200) is in the past and within 24 h

# Founder-approved gold rows from PR #7 (data/comments/labels.jsonl, not merged into main yet): expected_action
# ask_customer. On main, c106 is still "áo đẹp ghê" (label other), so the rows are inlined here.
GOLD = {
    "c019": {
        "ts_ms": 50080,
        "user_ref": "u064",
        "text": "lấy đôi này",
        "order": {"sku": "GIAY-01", "size": None, "color": None, "qty": 1, "referenced_card_id": "card-01"},
    },
    "c106": {
        "ts_ms": 285200,
        "user_ref": "u083",
        "text": "size như bạn trên nha shop",
        "order": {"sku": "AO-07", "size": None, "color": None, "qty": 1, "referenced_card_id": "card-05"},
    },
}


def cards() -> list[dict]:
    rows = [json.loads(x) for x in (ROOT / "data" / "comments" / "cards.jsonl").read_text().splitlines() if x]
    return [r for r in rows if r["event_id"] in ("k-card-01-v1", "k-card-05-v1")]


def commented(cid: str, user: str, text: str, ts: int, order: dict) -> list[dict]:
    """A comment and its comment_labeled(order) as the comments stream posts them."""
    base = {"source": "replay", "v": 1}
    cm = {
        **base,
        "event_id": f"cm-{cid}",
        "ts_ms": ts,
        "type": "comment",
        "comment_id": cid,
        "user_ref": user,
        "text": text,
        "reply_to": None,
    }
    lb = {
        **base,
        "event_id": f"lb-{cid}",
        "ts_ms": ts + 500,
        "type": "comment_labeled",
        "comment_id": cid,
        "label": "order",
        "order_draft": {
            "sku": order["sku"],
            "variant": {"size": order["size"], "color": order["color"]},
            "qty": order["qty"],
            "referenced_card_id": order["referenced_card_id"],
            "reference_kind": "current_card",
        },
        "scam_signals": [],
        "action": "none",
        "latency_ms": 500,
        "caused_by": [f"cm-{cid}"],
    }
    return [cm, lb]


def gold(cid: str, **order) -> list[dict]:
    row = GOLD[cid]
    return commented(cid, row["user_ref"], row["text"], row["ts_ms"], row["order"] | order)


async def play(client, events: list[dict]) -> tuple[Runner, list[dict]]:
    runner = Runner("http://test", handlers.HANDLERS, http=client)
    produced = []
    for _, e in replay.plan(events, SID, start_ms=now_ms() - START):
        assert (await client.post(f"/api/sessions/{SID}/events", json=[e])).status_code == 200
        produced += await runner.handle(e)
    assert runner.failures == 0
    return runner, produced


async def say(client, runner, thread: str, text: str, quick_reply: str | None = None) -> list[dict]:
    """A customer message through the sim Page webhook, handled as the worker would."""
    body = webhook_body(thread, now_ms(), text=text, quick_reply=quick_reply)
    [mi] = (await client.post(f"/api/sessions/{SID}/sim/messenger", json=body)).json()["events"]
    out = await runner.handle(mi)
    assert runner.failures == 0
    return out


async def stored(store) -> list[dict]:
    return await store.read(SID, 0, 2000)


def order_events(events: list[dict], order_id: str | None = None) -> list[dict]:
    return [e for e in events if e["type"] == "order" and order_id in (None, e["order_id"])]


def sent(events: list[dict]) -> list[str]:
    return [e["template"] for e in events if e["type"] == "message_out"]


def never_confirmed(events: list[dict]) -> None:
    """Invariant (AGENTS.md rule 6): no CONFIRMED order, and no receipt, without the customer's confirmation."""
    assert all(e["state"] != "CONFIRMED" for e in order_events(events))
    assert "receipt" not in sent(events)


@pytest.fixture
async def c019(mem_app):
    _, store, client = mem_app
    runner, produced = await play(client, [*cards(), *gold("c019")])
    return runner, store, client, produced


# --- 1-2. c019 and c106 ask the customer; no order, no confirmation, no receipt ----------------------------


@pytest.mark.parametrize(
    "cid, thread, options",
    [("c019", "th-u064", ["38", "39", "40"]), ("c106", "th-u083", ["M", "L"])],
)
async def test_gold_comment_without_size_asks_the_customer(mem_app, cid, thread, options):
    _, store, client = mem_app
    _, produced = await play(client, [*cards(), *gold(cid)])
    assert [e.get("state") or e["template"] for e in produced] == ["ask_variant"]
    [ask] = produced
    assert (ask["type"], ask["template"], ask["thread_ref"]) == ("message_out", "ask_variant", thread)
    p = ask["payload"]
    assert p["order_id"] == f"o-{cid}" and p["missing"] == ["size"]
    assert [o["size"] for o in p["options"]] == options
    assert p["send_api"]["recipient"] == {"comment_id": cid}  # private reply to the comment
    assert [q["payload"] for q in p["send_api"]["message"]["quick_replies"]] == [
        f"PICK:o-{cid}#{i}" for i in range(len(options))
    ]
    assert all(len(q["title"]) <= 20 for q in p["send_api"]["message"]["quick_replies"])
    assert ask["caused_by"][0] == f"{SID}.lb-{cid}"
    events = await stored(store)
    assert order_events(events) == [] and sent(events) == ["ask_variant"]
    never_confirmed(events)


async def test_size_like_the_customer_above_is_not_copied_from_their_order(mem_app):
    """c106 "size như bạn trên": u082 just ordered size L; u083 is still asked, with M and L both offered."""
    _, store, client = mem_app
    above = commented("c105", "u082", "lấy size L", 284000, GOLD["c106"]["order"] | {"size": "L"})
    _, produced = await play(client, [*cards(), *above, *gold("c106")])
    events = await stored(store)
    assert [e["state"] for e in order_events(events, "o-c105")] == ["DRAFT", "CONFIRM_SENT"]
    [ask] = [e for e in produced if e["type"] == "message_out" and e["template"] == "ask_variant"]
    assert ask["thread_ref"] == "th-u083" and [o["size"] for o in ask["payload"]["options"]] == ["M", "L"]
    assert order_events(events, "o-c106") == []
    never_confirmed(events)


# --- 3. a valid answer drafts at the card price of the version the comment was priced from ----------------


async def test_pick_drafts_then_confirm_order_at_the_comment_card_price(c019):
    runner, store, client, _ = c019
    v2 = {**cards()[0], "event_id": "k-card-01-v2", "version": 2, "price": 520000, "ts_ms": START - 10_000}
    [(_, v2)] = replay.plan([v2], SID, start_ms=now_ms() - START)
    assert (await client.post(f"/api/sessions/{SID}/events", json=[v2])).status_code == 200

    out = await say(client, runner, "th-u064", "Size 39", "PICK:o-c019#1")
    draft, decide, msg = out
    assert [draft["state"], decide["state"]] == ["DRAFT", "CONFIRM_SENT"]
    line = decide["lines"][0]
    assert (line["card_version"], line["unit_price"], line["variant"]) == (
        1,
        450000,
        {"size": "39", "color": "trắng"},
    )
    assert "size 39 chosen by the customer in Messenger" in draft["reason"]
    ask = next(e for e in await stored(store) if e["type"] == "message_out")
    assert {f"{SID}.cm-c019", f"{SID}.k-card-01-v1", ask["event_id"]} <= set(draft["caused_by"])
    assert msg["template"] == "confirm_order" and msg["thread_ref"] == "th-u064"
    assert msg["payload"]["send_api"]["recipient"] == {"id": "u064"}  # the comment's private reply is used
    assert (msg["payload"]["unit_price"], msg["payload"]["card_version"]) == (450000, 1)
    never_confirmed(await stored(store))

    out = await say(client, runner, "th-u064", "Đồng ý", "CONFIRM:o-c019")
    assert (out[0]["state"], out[0]["actor"], out[0]["receipt"]["price"]) == ("CONFIRMED", "customer", 450000)
    assert out[1]["template"] == "receipt"


@pytest.mark.parametrize(
    "text, size",
    [
        ("size 39 nha shop", "39"),
        ("Lấy 40 nha", "40"),
        ("38", "38"),
        ("Mình lấy đôi 39 nha shop", "39"),
        ("ko huỷ nha, size 39", "39"),
        ("cỡ 40 ạ", "40"),
    ],
)
async def test_text_naming_one_size_drafts(c019, text, size):
    runner, store, client, _ = c019
    out = await say(client, runner, "th-u064", text)
    assert [e["state"] for e in order_events(out)] == ["DRAFT", "CONFIRM_SENT"]
    assert out[1]["lines"][0]["variant"]["size"] == size and sent(out) == ["confirm_order"]
    never_confirmed(await stored(store))


async def test_c106_text_answer_drafts_size_m(mem_app):
    _, store, client = mem_app
    runner, _ = await play(client, [*cards(), *gold("c106")])
    out = await say(client, runner, "th-u083", "mình lấy size M nha")
    assert out[1]["state"] == "CONFIRM_SENT" and out[1]["lines"][0]["unit_price"] == 550000
    assert out[1]["lines"][0]["variant"] == {"size": "M", "color": "xanh"}
    never_confirmed(await stored(store))


# --- 4. an answer that is not one variant of the card asks again, no order ----------------------------------


@pytest.mark.parametrize(
    "text, quick_reply",
    [
        ("size 45", None),
        ("cảm ơn shop", None),
        ("38 hay 39 ta", None),
        ("giống bạn trên", None),
        ("ok", None),
        ("Size 99", "PICK:o-c019#7"),
        ("Size 39", "PICK:o-c019#x"),
        # a size the customer refuses, or a number that is an address or a question, is not a pick
        ("không lấy size 40 đâu", None),
        ("ko lấy 39 nha", None),
        ("giao 40 Lê Lợi nha shop", None),
        ("mình ở số 39 Trần Phú", None),
        ("450k hả shop, 39 còn không", None),
        ("size 39, giao 40 Lê Lợi", None),
    ],
)
async def test_unclear_answer_asks_again(c019, text, quick_reply):
    runner, store, client, _ = c019
    [again] = await say(client, runner, "th-u064", text, quick_reply)
    assert (again["template"], again["thread_ref"]) == ("ask_variant", "th-u064")
    assert again["payload"]["text"].startswith("Shop chưa rõ bạn chọn size nào")
    assert again["payload"]["send_api"]["recipient"] == {"id": "u064"}
    events = await stored(store)
    assert order_events(events) == []
    never_confirmed(events)


async def test_asks_at_most_three_times(c019):
    runner, store, client, _ = c019
    assert sent(await say(client, runner, "th-u064", "cảm ơn shop")) == ["ask_variant"]
    assert sent(await say(client, runner, "th-u064", "hmm")) == ["ask_variant"]
    assert await say(client, runner, "th-u064", "để mình xem") == []
    events = await stored(store)
    assert sent(events) == ["ask_variant"] * 3 and order_events(events) == []
    # the open question can still be answered
    out = await say(client, runner, "th-u064", "Size 38", "PICK:o-c019#0")
    assert out[1]["state"] == "CONFIRM_SENT"


async def test_a_cancel_instead_of_a_size_is_not_asked_again(c019):
    runner, store, client, _ = c019
    assert await say(client, runner, "th-u064", "thôi không lấy nữa") == []
    assert order_events(await stored(store)) == []


# --- 5. only the customer who was asked can answer ---------------------------------------------------------


async def test_another_customer_cannot_answer_or_order_through_the_question(c019):
    runner, store, client, _ = c019
    assert await say(client, runner, "th-u099", "size 39") == []
    assert await say(client, runner, "th-u099", "Size 39", "PICK:o-c019#1") == []
    events = await stored(store)
    assert order_events(events) == [] and sent(events) == ["ask_variant"]
    out = await say(client, runner, "th-u064", "Size 40", "PICK:o-c019#2")
    assert {e["customer_ref"] for e in order_events(out)} == {"u064"}
    assert out[1]["lines"][0]["variant"]["size"] == "40"
    # a second tap changes nothing
    assert await say(client, runner, "th-u064", "Size 38", "PICK:o-c019#0") == []
    never_confirmed(await stored(store))


async def test_two_open_questions_in_one_thread_text_never_picks(mem_app):
    _, store, client = mem_app
    second = commented("c107", "u064", "áo này nữa", 285500, GOLD["c106"]["order"])
    runner, produced = await play(client, [*cards(), *gold("c019"), *second])
    assert sent(produced) == ["ask_variant", "ask_variant"]
    [again] = await say(client, runner, "th-u064", "size 39")
    assert again["template"] == "ask_variant" and again["payload"]["order_id"] == "o-c107"
    assert order_events(await stored(store)) == []
    out = await say(client, runner, "th-u064", "Size 39", "PICK:o-c019#1")
    assert out[0]["order_id"] == "o-c019" and out[1]["state"] == "CONFIRM_SENT"


# --- 7. one variant, a named size, short stock: unchanged -------------------------------------------------


async def test_named_size_drafts_without_asking(mem_app):
    _, store, client = mem_app
    _, produced = await play(client, [*cards(), *gold("c019", size="39")])
    assert [e["state"] for e in order_events(produced)] == ["DRAFT", "CONFIRM_SENT"]
    assert sent(produced) == ["confirm_order"]


async def test_named_size_short_on_stock_goes_to_the_seller(mem_app):
    _, store, client = mem_app
    _, produced = await play(client, [*cards(), *gold("c019", size="39", qty=3)])
    assert [e["state"] for e in order_events(produced)] == ["DRAFT", "NEEDS_SELLER"]
    assert sent(produced) == ["waiting_for_shop"]
    never_confirmed(await stored(store))


def card_scene(variants_: list[dict]) -> tuple[dict, flow.View]:
    """Card Y with `variants_` and an order comment from uy that names no size."""
    card = {
        "event_id": "card-y1",
        "type": "card",
        "ts_ms": 1000,
        "card_id": "card-y",
        "version": 1,
        "status": "live",
        "sku": "Y-1",
        "name": "Túi Y",
        "price": 200000,
        "image_url": "sim://y.png",
        "variants": variants_,
    }
    order = GOLD["c019"]["order"] | {"referenced_card_id": "card-y"}
    [cm, lb] = commented("c1", "uy", "lấy cái này", 5000, order)
    return lb, flow.View.of([card, cm, lb])


TWO_SIZES = [{"size": "S", "color": "đỏ", "stock": 1}, {"size": "M", "color": "đỏ", "stock": 1}]


class Recorder:
    async def send(self, thread_ref, template, payload):
        return {"type": "message_out", "thread_ref": thread_ref, "template": template, "payload": payload}


async def test_single_variant_card_drafts_as_before():
    lb, view = card_scene([{"size": None, "color": "đỏ", "stock": 4}])
    out = await flow.on_labeled(lb, view, Recorder(), 6000)
    assert [e.get("state") for e in out] == ["DRAFT", "CONFIRM_SENT", None] and sent(out) == ["confirm_order"]


async def test_picked_size_sold_since_the_question_goes_to_the_seller():
    """The pick is checked against stock held by other orders at pick time; the rules send it to the seller."""
    lb, view = card_scene(TWO_SIZES)
    [ask] = await flow.on_labeled(lb, view, Recorder(), 6000)
    line = {
        "sku": "Y-1",
        "card_id": "card-y",
        "card_version": 1,
        "variant": {"size": "S", "color": "đỏ"},
        "qty": 1,
    }
    other = {
        "event_id": "o-z.sent",
        "type": "order",
        "ts_ms": 6500,
        "order_id": "o-z",
        "customer_ref": "uz",
        "lines": [line],
        "state": "CONFIRM_SENT",
    }
    mi = {
        "event_id": "mi-1",
        "type": "message_in",
        "ts_ms": 7000,
        "thread_ref": "th-uy",
        "template": None,
        "payload": {"text": "size S"},
    }
    view = flow.View.of([*view.cards, view.comments["c1"]["comment"], lb, ask, other, mi])
    out = await flow.on_reply(mi, view, Recorder(), 7000)
    assert [e.get("state") for e in out] == ["DRAFT", "NEEDS_SELLER", None]
    assert sent(out) == ["waiting_for_shop"] and "stock" in out[1]["reason"]


# --- extras: replay, 24 h window, webhook, C1, source log ---------------------------------------------------


async def test_replayed_label_asks_once():
    lb, view = card_scene(TWO_SIZES)
    [ask] = await flow.on_labeled(lb, view, Recorder(), 6000)
    again = flow.View.of([*view.cards, view.comments["c1"]["comment"], lb, ask])
    assert await flow.on_labeled(lb, again, Recorder(), 6100) == []


async def test_outside_the_24h_window_nothing_is_asked_and_the_seller_sees_it():
    later = 5000 + 25 * 3600 * 1000
    lb, view = card_scene(TWO_SIZES)
    out = await flow.on_labeled(lb, view, Recorder(), later)
    assert [e.get("state") for e in out] == ["DRAFT", "NEEDS_SELLER"] and sent(out) == []


def test_pick_quick_reply_round_trip_through_the_webhook():
    [m] = parse_webhook(webhook_body("th-u064", 1000, text="Size 39", quick_reply="PICK:o-c019#1"))
    assert (m["thread_ref"], m["template"]) == ("th-u064", "pick_variant")
    assert m["payload"] == {"text": "Size 39", "order_id": "o-c019", "quick_reply": "PICK:o-c019#1"}
    assert customer_reply(m["template"], m["payload"]) == ("pick", "o-c019")
    assert customer_reply(None, {"quick_reply": "PICK:o-c019#1"}) == ("pick", "o-c019")
    [m] = parse_webhook(webhook_body("th-u064", 1000, text="Đồng ý", quick_reply="CONFIRM:o-c019"))
    assert m["payload"]["order_id"] == "o-c019"


async def test_the_label_has_a_consequence_and_the_log_shows_the_question(c019):
    runner, store, client, _ = c019
    events = await stored(store)
    assert c1([e for e in events if e["type"] != "comment_labeled" or e["label"] == "order"])["missing"] == []
    await say(client, runner, "th-u064", "Size 39", "PICK:o-c019#1")
    log = (await client.get(f"/api/sessions/{SID}/orders/o-c019/log")).json()
    assert log["comment"]["text"] == "lấy đôi này" and log["card"]["version"] == 1
    assert [m["template"] for m in log["messages"] if m["type"] == "message_out"] == [
        "ask_variant",
        "confirm_order",
    ]
    assert any(m["type"] == "message_in" for m in log["messages"])


@pytest.mark.parametrize(
    "text, value, want",
    [
        ("màu đen nha", "đen", True),
        ("mau den nha", "đen", True),
        ("đến tối mình lấy", "đen", False),
        ("size 39 nha", "39", True),
        ("sđt 0939123456", "39", False),
        ("size M", "M", True),
        ("mình lấy", "M", False),
    ],
)
def test_whole_word_match(text, value, want):
    from orders import variants  # not at the top, so this file also runs on main for the red/green check

    assert variants.says(text, value) is want


def test_missing_color_only_when_colors_differ():
    from orders import variants

    card = {"variants": [{"size": "39", "color": "đen"}, {"size": "39", "color": "trắng"}]}
    want = {"size": "39", "color": None}
    hits = variants.hit_indexes(card, want)
    assert hits == [0, 1] and variants.missing_fields(card, hits, want) == ["color"]
    assert variants.named("màu trắng nha", card, hits, ["color"]) == [1]
