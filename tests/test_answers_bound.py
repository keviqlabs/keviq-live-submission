"""In a session bound to an attachment, the server's reconcile pass replies to price and stock questions.

The event worker cannot write message_out there (answers/handlers.py stays silent), so the reply comes from
app/attachment_orders.reconcile: one message_out per question, carrying that question's comment_id.
"""

from app.contracts import make_event
from tests.test_attachments_runtime import world  # noqa: F401  (the bound-session fixture)


async def ask(w, cid, label, text, ts):
    comment = make_event(
        "comment",
        w.sid,
        ts,
        "sim",
        comment_id=cid,
        user_ref="synthetic-buyer-" + cid,
        text=text,
        reply_to=None,
    )
    await w.ingest(comment)
    labeled = make_event(
        "comment_labeled", w.sid, ts, "sim", comment_id=cid, label=label, order_draft=None, scam_signals=[],
        action="none", latency_ms=0, caused_by=[comment["event_id"]],
    )  # fmt: skip
    await w.ingest(labeled)
    return labeled


async def replies(w):
    return [e for e in await w.events() if e["type"] == "message_out"]


async def test_bound_session_replies_once_to_each_price_and_stock_question(world):  # noqa: F811
    w = world
    card = await w.card(10, "GIAY-01", 150_000)
    stock = await ask(w, "c-stock", "stock_question", "size 38 còn không shop", 5_000)
    price = await ask(w, "c-price", "price_question", "giá bao nhiêu shop", 5_100)
    await w.service.expire_due(w.sid)

    out = await replies(w)
    by = {m["payload"]["comment_id"]: m for m in out}
    assert len(out) == 2 and set(by) == {"c-stock", "c-price"}
    assert by["c-stock"]["payload"]["send_api"]["recipient"] == {"comment_id": "c-stock"}
    assert by["c-stock"]["template"] == "answer_question" and by["c-stock"]["payload"]["kind"] == "stock"
    assert "size 38 còn 5" in by["c-stock"]["payload"]["text"]
    assert (
        by["c-price"]["payload"]["kind"] == "price" and "150 nghìn đồng" in by["c-price"]["payload"]["text"]
    )
    assert all(m["payload"]["card_id"] == card["card_id"] for m in out)
    assert (
        by["c-stock"]["caused_by"][0] == stock["event_id"]
        and by["c-price"]["caused_by"][0] == price["event_id"]
    )
    assert {stock["event_id"], price["event_id"]} <= await w.store.consumed_attachment_inputs(w.sid)

    await w.service.expire_due(w.sid)
    assert len(await replies(w)) == 2


async def test_bound_session_defers_a_stock_count_it_does_not_have(world):  # noqa: F811
    w = world
    await w.card(10, None, 150_000)
    await ask(w, "c-stock", "stock_question", "còn hàng không shop", 5_000)
    await w.service.expire_due(w.sid)
    (m,) = await replies(w)
    assert m["payload"]["comment_id"] == "c-stock" and m["payload"]["kind"] == "stock"
    assert "để người bán xác nhận" in m["payload"]["text"] and not any(
        ch.isdigit() for ch in m["payload"]["text"]
    )
