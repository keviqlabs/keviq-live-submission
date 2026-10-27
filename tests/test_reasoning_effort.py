"""Reasoning effort per call: the frame read (product_seen) asks for "minimal"; the speech read and any call
that does not ask keep the model's default, so their request has no `reasoning`. The comment labels are
covered in tests/test_labels.py. FakeTransport only, no model call.
"""

import json
from types import SimpleNamespace

from meta.transport import responses_reply
from tests.test_meta import SCHEMA, make
from tests.test_vision import answer, fresh, run_frames  # noqa: F401
from worker.brain import card


async def test_frame_read_asks_for_minimal_effort(mem_app):
    _, transport = await run_frames(mem_app, [answer("sneaker", "trắng")])
    [(_, body)] = transport.requests
    assert body["text"]["format"]["name"] == "product_seen"
    assert body["reasoning"] == {"effort": "minimal"}


async def test_speech_read_keeps_the_default_effort():
    reply = responses_reply(json.dumps({"price_vnd": 450000, "sizes": [], "colors": []}))
    client, transport, _, _ = make([reply])
    ctx = SimpleNamespace(client=lambda: client)
    spoken, _ = await card.read_speech("đôi này 450 nghìn", ctx)
    [(_, body)] = transport.requests
    assert spoken.price == 450000 and body["text"]["format"]["name"] == "card_from_speech"
    assert "reasoning" not in body


async def test_a_call_that_does_not_ask_sends_no_reasoning():
    client, transport, _, _ = make([responses_reply(json.dumps({"label": "order"}))])
    await client.responses("x", SCHEMA, purpose="p")
    assert "reasoning" not in transport.requests[0][1]
