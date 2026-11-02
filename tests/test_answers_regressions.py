"""Session isolation and exact colour/size stock for current-item replies. No network or DB."""

import copy

import pytest

from answers import handlers
from answers.facts import facts_from_card
from answers.questions import parse
from answers.reply import check, compose
from answers.selector import Selector
from app.contracts import make_event
from app.store import MemoryStore
from tests.test_answers_bound import replies
from tests.test_answers_message import Ctx, card, question
from tests.test_attachments_runtime import World, world  # noqa: F401


async def ask(w, cid, label, text, ts):
    comment = make_event(
        "comment", w.sid, ts, "sim", comment_id=cid, user_ref="synthetic-buyer", text=text, reply_to=None
    )
    await w.ingest(comment)
    labeled = make_event(
        "comment_labeled",
        w.sid,
        ts,
        "sim",
        comment_id=cid,
        label=label,
        order_draft=None,
        scam_signals=[],
        action="none",
        latency_ms=0,
        caused_by=[comment["event_id"]],
    )
    await w.ingest(labeled)
    return labeled


@pytest.mark.parametrize("cid", ["same-comment", "x" * 96])
async def test_unbound_reply_identity_is_per_session_and_idempotent(monkeypatch, cid):
    store = MemoryStore()
    await store.open()
    sessions = ["a" * 64, "b" * 64]
    events_by_session = {}
    for index, sid in enumerate(sessions):
        comment, label = question("size 38 còn không", cid=cid)
        events = [card(), comment, label]
        for e in events:
            e["session_id"] = sid
        comment["event_id"], label["event_id"] = f"comment-{index}", f"label-{index}"
        events_by_session[sid] = events

    async def load(ctx, sid):
        return events_by_session[sid]

    monkeypatch.setattr(handlers, "load_events", load)
    ids = []
    for sid in sessions:
        label = events_by_session[sid][-1]
        (out,) = await handlers.on_labeled(label, Ctx(bound=False))
        fields = {k: v for k, v in out.items() if k not in ("type", "ts_ms")}
        event = make_event("message_out", sid, out["ts_ms"], "replay", **fields)
        assert (await store.append(event))[1]
        assert not (await store.append(event))[1]
        ids.append(event["event_id"])
        # A retry before the output is loaded derives the same ID; after loading it there is no second reply.
        (retry,) = await handlers.on_labeled(label, Ctx(bound=False))
        assert retry["event_id"] == event["event_id"]
        events_by_session[sid].append(event)
        assert await handlers.on_labeled(label, Ctx(bound=False)) == []
        assert len(await store.read(sid)) == 1
    assert len(set(ids)) == 2


@pytest.mark.parametrize("cid", ["same-comment", "x" * 96])
async def test_bound_same_comment_in_two_sessions_keeps_canonical_replies(world, cid):  # noqa: F811
    first = world
    second = World(first.app, first.store, first.client)
    second.sid = "s" * 64
    second.source, second.system = "synthetic-source-second", "synthetic-system-second"
    await second.configure()
    for w in (first, second):
        await w.card(10, "GIAY-01", 150000)
        labeled = await ask(w, cid, "stock_question", "size 38 còn không", 5000)
        await w.service.expire_due(w.sid)
        (out,) = await replies(w)
        assert out["payload"]["comment_id"] == cid
        assert labeled["event_id"] in await w.store.consumed_attachment_inputs(w.sid)
        await w.service.expire_due(w.sid)
        assert len(await replies(w)) == 1
    assert (await replies(first))[0]["event_id"] != (await replies(second))[0]["event_id"]


def multicolour_card():
    return card(
        sku="GIAY-04",
        name="Sandal quai ngang",
        variants=[
            {"size": "38", "color": "đen", "stock": 0},
            {"size": "38", "color": "kem", "stock": 4},
            {"size": "39", "color": "đen", "stock": 2},
            {"size": "39", "color": "kem", "stock": 0},
        ],
    )


@pytest.mark.parametrize("color,size,n", [("đen", "38", 0), ("kem", "38", 4), ("đen", "39", 2)])
def test_stock_count_uses_requested_colour_and_size(color, size, n):
    facts = facts_from_card(multicolour_card())
    q = parse(f"Size {size} màu {color} còn mấy đôi shop?", "stock_question")
    answer = compose(q, facts)
    assert answer.action == "answer"
    assert f"màu {color} size {size}" in answer.text
    assert (f"còn {n}" if n else "hết hàng") in answer.text
    assert check(answer, facts) is None


@pytest.mark.parametrize(
    "text",
    [
        "Size 38 còn mấy đôi shop?",
        "Size 38 màu bạc còn không?",
        "Size 38 màu đen nhạt còn không?",
        "Size 38 màu đen và kem còn không?",
        "Size 37 màu đen còn không?",
        "Size 38 hoặc size 39 màu đen còn không?",
    ],
)
def test_unclear_or_missing_requested_variant_defers(text):
    facts = facts_from_card(multicolour_card())
    answer = compose(parse(text, "stock_question"), facts)
    assert answer.action == "defer"
    assert "người bán xác nhận" in answer.text
    assert not any(ch.isdigit() for ch in answer.text)
    assert check(answer, facts) is None


def test_color_query_cannot_borrow_a_single_colour_cards_stock():
    facts = facts_from_card(card())
    answer = compose(parse("size 38 màu kem còn không?", "stock_question"), facts)
    assert answer.action == "defer" and "còn 2" not in answer.text


def test_recheck_rejects_another_colours_count_or_name():
    facts = facts_from_card(multicolour_card())
    answer = compose(parse("size 38 màu đen còn không?", "stock_question"), facts)
    assert check(answer, facts, "Sandal quai ngang màu đen size 38 còn 4 ạ.") is not None
    assert check(answer, facts, "Sandal quai ngang màu kem size 38 hết hàng rồi ạ.") is not None


def test_selector_does_not_group_different_colours_together():
    s = Selector(window_ms=1000, cooldown_ms=0, guard_ms=0)
    s.card(copy.deepcopy(multicolour_card()), 0)
    s.comment("black", 4000, "size 38 màu đen còn không", "stock_question")
    s.comment("cream", 4100, "size 38 màu kem còn không", "stock_question")
    out = s.due(5100)
    assert len(out) == 2
    by = {d.comment_ids: d for d in out}
    assert "hết hàng" in by[("black",)].text
    assert "còn 4" in by[("cream",)].text


def test_requested_size_is_not_replaced_with_an_unsized_count():
    facts = facts_from_card(
        card(
            sku="SP07",
            name="Tay cầm",
            variants=[
                {"size": None, "color": "đen", "stock": 4},
            ],
        )
    )
    answer = compose(parse("size 38 màu đen còn không?", "stock_question"), facts)
    assert answer.action == "defer" and "còn 4" not in answer.text
