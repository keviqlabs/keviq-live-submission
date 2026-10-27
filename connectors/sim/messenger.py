"""Simulated Messenger with the same interface as connectors/facebook will expose.

No network calls. `send()` returns the message_out fields; `webhook()` turns a simulated customer reply into
message_in fields. Real Send API / webhook payload mapping is out of P0 until the 30/10 gate.
"""

from connectors.facebook.interface import Messenger


class SimMessenger(Messenger):
    def __init__(self):
        self.sent: list[dict] = []
        self.last_customer_ms: dict[str, int] = {}

    async def send(self, thread_ref: str, template: str, payload: dict) -> dict:
        out = {"type": "message_out", "thread_ref": thread_ref, "template": template, "payload": payload}
        self.sent.append(out)
        return out

    def webhook(self, thread_ref: str, template: str | None, payload: dict, ts_ms: int) -> dict:
        self.last_customer_ms[thread_ref] = ts_ms
        return {"type": "message_in", "thread_ref": thread_ref, "template": template, "payload": payload}
