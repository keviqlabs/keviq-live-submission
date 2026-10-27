# orders/

Order state machine (`states.py`), permission rules (`rules.py`), the order flow (`flow.py`), worker handlers
(`handlers.py`) and the reading of customer text (`intent.py`).

## Flow

1. `comment_labeled` with `label == order` → `DRAFT`, priced from the card version that was live at the comment's
   `ts_ms`, stock checked per variant (orders in `CONFIRM_SENT`, `CONFIRMED`, `CLOSED` hold stock).
2. `rules.evaluate_draft` decides. Allowed → `CONFIRM_SENT` and a `confirm_order` message (private reply to the
   comment) with size, color, qty, unit price, amount, address request and payment method (COD).
   Discount request, amount over the threshold or short stock → `NEEDS_SELLER` and a
   `waiting_for_shop` message.
3. The seller decides in the console: approve at the card price, approve at a new unit price
   (`rules.price_change`), or reject (`CANCELLED` by the seller). Approve → `CONFIRM_SENT` by the seller.
4. The customer's `message_in` (quick reply `CONFIRM:<order_id>` / `CANCEL:<order_id>`, or a plain yes/no in the
   thread) → `CONFIRMED` with a receipt `{card_id, card_version, image_url, price}` from that card version, and a
   receipt message; or `CANCELLED` by the customer.

Every step is an `order` event whose `caused_by` points at the comment, the card version, the previous order event
and the customer message. `GET /api/sessions/{sid}/orders/{order_id}/log` returns that chain for the console (C6).

## Console API (`app/orders_api.py`)

- `POST /api/sessions/{sid}/orders/{order_id}/decision` `{"decision": "approve"|"reject", "unit_price"?: int}`
- `GET /api/sessions/{sid}/orders/{order_id}/log`
- `POST /api/sessions/{sid}/sim/messenger` — a Page webhook body from a simulated customer; refused (403) in a
  session that has `facebook` events.

## Limits

- No seller identity on the decision endpoint: local demo only.
- Approve does not re-check stock taken by other orders since the draft.
- Static VietQR is not built.
- Fixture triggers and fixture orders (`fx-`) are left alone, so a replay with fixtures shows the recorded orders.
