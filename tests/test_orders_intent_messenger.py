"""orders/intent.py (discount requests, yes/no) and connectors/sim/messenger.py (Send API / webhook shapes)."""

import pytest

from connectors.sim.messenger import SimMessenger, parse_webhook, send_api_body, webhook_body
from orders.intent import customer_reply, discount_request


@pytest.mark.parametrize(
    "text, kind, value",
    [
        ("Lấy đôi vừa rồi, size 39, hai đôi giảm còn 400 được không?", "target", 400000),
        ("bớt 50k nha shop", "reduction", 50000),
        ("giảm giá còn 1tr2 không", "target", 1200000),
        ("bớt 1tr nha", "reduction", 1000000),
        ("giảm còn 350.000 được không", "target", 350000),
        ("fix giá 380 nghìn", "target", 380000),
        ("giảm chút đi shop", "unspecified", None),
        ("giảm 10% nha", "unspecified", None),
        ("Chốt túi đỏ 1 cái", None, None),
        ("size 39 còn không", None, None),  # "còn" without a discount word is a stock question
        ("", None, None),
    ],
)
def test_discount_request(text, kind, value):
    got = discount_request(text)
    if kind is None:
        assert got is None
    else:
        assert (got["kind"], got["value"]) == (kind, value) and got["phrase"] in text


def test_discount_phrase_keeps_the_discount_word():
    assert discount_request("hai đôi giảm còn 400 được không?")["phrase"] == "giảm còn 400"


@pytest.mark.parametrize(
    "template, payload, want",
    [
        ("confirm_yes", {"order_id": "o-06"}, ("yes", "o-06")),
        ("confirm_no", {"order_id": "o-06"}, ("no", "o-06")),
        (None, {"text": "Đồng ý", "quick_reply": "CONFIRM:o-05"}, ("yes", "o-05")),
        (None, {"text": "Huỷ", "quick_reply": "CANCEL:o-05"}, ("no", "o-05")),
        (None, {"text": "Đồng ý!"}, ("yes", None)),
        (None, {"text": "không"}, ("no", None)),
        (None, {"text": "không biết nữa, để mình hỏi chồng"}, (None, None)),
        (None, {"text": "ok mà giảm thêm được không"}, (None, None)),
    ],
)
def test_customer_reply(template, payload, want):
    assert customer_reply(template, payload) == want


def test_send_api_body_private_reply_then_thread():
    first = send_api_body("th-u05", text="hi", comment_id="cm-05", quick_replies=[("Đồng ý", "CONFIRM:o-05")])
    assert first["recipient"] == {"comment_id": "cm-05"} and first["messaging_type"] == "RESPONSE"
    assert first["message"]["quick_replies"] == [
        {"content_type": "text", "title": "Đồng ý", "payload": "CONFIRM:o-05"}
    ]
    later = send_api_body("th-u05", elements=[{"title": "Giày", "image_url": "sim://a.png", "subtitle": "x"}])
    assert later["recipient"] == {"id": "u05"}
    assert later["message"]["attachment"]["payload"]["template_type"] == "generic"


def test_webhook_round_trip_and_echoes_skipped():
    body = webhook_body("th-u05", 1000, text="Đồng ý", quick_reply="CONFIRM:o-05")
    [m] = parse_webhook(body)
    assert (m["thread_ref"], m["template"], m["ts_ms"]) == ("th-u05", "confirm_yes", 1000)
    assert m["payload"] == {"text": "Đồng ý", "order_id": "o-05", "quick_reply": "CONFIRM:o-05"}
    body["entry"][0]["messaging"][0]["message"]["is_echo"] = True
    assert parse_webhook(body) == []
    assert parse_webhook({"object": "instagram", "entry": []}) == []


async def test_sim_messenger_send_carries_the_send_api_body():
    sim = SimMessenger()
    out = await sim.send(
        "th-u07",
        "confirm_order",
        {"order_id": "o-07", "text": "x", "quick_replies": [["Huỷ", "CANCEL:o-07"]]},
    )
    assert out["type"] == "message_out" and "quick_replies" not in out["payload"]
    assert out["payload"]["send_api"]["message"]["quick_replies"][0]["payload"] == "CANCEL:o-07"
    assert sim.sent == [out]
