"""Simulated Messenger with the same interface as connectors/facebook will expose.

No network calls. Outgoing messages carry the body the Send API would receive (`payload["send_api"]`, POST
/{page-id}/messages); simulated customer replies come in as a Page webhook body (`object: "page"`,
`entry[].messaging[]`) and `parse_webhook()` turns them into message_in fields. Thread refs are `th-<user_ref>`;
the PSID in the sim is the user_ref. Quick reply payloads are `CONFIRM:<order_id>` / `CANCEL:<order_id>` /
`KEEP:<order_id>` (`KEEP:all` keeps every order).
"""

from connectors.facebook.interface import Messenger

QUICK_REPLY_TEMPLATES = {
    "CONFIRM": "confirm_yes",
    "CANCEL": "confirm_no",
    "KEEP": "keep_order",
    "PICK": "pick_variant",  # PICK:<order_id>#<variant index>; order ids never contain "#"
}


def psid(thread_ref: str) -> str:
    return thread_ref.removeprefix("th-")


def send_api_body(
    thread_ref: str,
    text: str | None = None,
    quick_replies: list[tuple[str, str]] = (),
    comment_id: str | None = None,
    elements: list[dict] | None = None,
) -> dict:
    """Send API request body. A first reply to a comment goes by `comment_id` (private reply)."""
    message: dict = {}
    if elements:
        message["attachment"] = {
            "type": "template",
            "payload": {"template_type": "generic", "elements": elements},
        }
    else:
        message["text"] = text or ""
    if quick_replies:
        message["quick_replies"] = [
            {"content_type": "text", "title": title, "payload": payload} for title, payload in quick_replies
        ]
    recipient = {"comment_id": comment_id} if comment_id else {"id": psid(thread_ref)}
    return {"recipient": recipient, "messaging_type": "RESPONSE", "message": message}


def webhook_body(
    thread_ref: str,
    ts_ms: int,
    text: str | None = None,
    quick_reply: str | None = None,
    page_id: str = "sim-page",
    mid: str | None = None,
) -> dict:
    """Page webhook body for one customer message (what Meta would POST to the callback URL)."""
    message: dict = {"mid": mid or f"m_{psid(thread_ref)}_{ts_ms}", "text": text or ""}
    if quick_reply:
        message["quick_reply"] = {"payload": quick_reply}
    messaging = {
        "sender": {"id": psid(thread_ref)},
        "recipient": {"id": page_id},
        "timestamp": ts_ms,
        "message": message,
    }
    return {"object": "page", "entry": [{"id": page_id, "time": ts_ms, "messaging": [messaging]}]}


def parse_webhook(body: dict) -> list[dict]:
    """message_in fields (with ts_ms and mid) for each customer message in a Page webhook body."""
    if body.get("object") != "page":
        return []
    out = []
    for entry in body.get("entry") or []:
        for m in entry.get("messaging") or []:
            msg = m.get("message") or {}
            sender = (m.get("sender") or {}).get("id")
            if not sender or msg.get("is_echo") or not isinstance(m.get("timestamp"), int):
                continue
            template, payload = None, {"text": msg.get("text") or ""}
            qr = (msg.get("quick_reply") or {}).get("payload") or ""
            kind, _, order_id = qr.partition(":")
            if kind in QUICK_REPLY_TEMPLATES and order_id:
                template = QUICK_REPLY_TEMPLATES[kind]
                payload |= {"order_id": order_id.partition("#")[0], "quick_reply": qr}
            out.append(
                {
                    "type": "message_in",
                    "ts_ms": m["timestamp"],
                    "mid": msg.get("mid"),
                    "thread_ref": f"th-{sender}",
                    "template": template,
                    "payload": payload,
                }
            )
    return out


class SimMessenger(Messenger):
    def __init__(self):
        self.sent: list[dict] = []
        self.last_customer_ms: dict[str, int] = {}

    async def send(self, thread_ref: str, template: str, payload: dict) -> dict:
        """message_out fields; `payload["send_api"]` is built from text / quick_replies / comment_id / elements."""
        p = dict(payload)
        body = send_api_body(
            thread_ref,
            text=p.get("text"),
            quick_replies=[tuple(q) for q in p.pop("quick_replies", [])],
            comment_id=p.get("comment_id"),
            elements=p.pop("elements", None),
        )
        out = {
            "type": "message_out",
            "thread_ref": thread_ref,
            "template": template,
            "payload": p | {"send_api": body},
        }
        self.sent.append(out)
        return out

    def webhook(self, thread_ref: str, template: str | None, payload: dict, ts_ms: int) -> dict:
        self.last_customer_ms[thread_ref] = ts_ms
        return {"type": "message_in", "thread_ref": thread_ref, "template": template, "payload": payload}
