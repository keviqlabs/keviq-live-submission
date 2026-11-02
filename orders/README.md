# orders/

Order state machine (`states.py`), permission rules (`rules.py`), the order flow (`flow.py`), worker handlers
(`handlers.py`), the reading of customer text (`intent.py`) and which card variant a comment means (`variants.py`).

## Flow

1. `comment_labeled` with `label == order` → `DRAFT`, priced from the card version it is locked to (see "Card
   version lock" below), stock checked per variant (orders in `CONFIRM_SENT`, `CONFIRMED`, `CLOSED` hold stock). If the comment
   matches several variants and does not say the size or color that tells them apart, the customer is asked first
   (see below) and the draft waits for the answer.
2. `rules.evaluate_draft` decides. Allowed → `CONFIRM_SENT` and a `confirm_order` message (private reply to the
   comment) with size, color, qty, unit price, amount, the locked version's `image_url`, address request and
   payment method (COD).
   Discount request, amount over the threshold or short stock → `NEEDS_SELLER` and a
   `waiting_for_shop` message.
3. The seller decides in the console: approve at the card price, approve at a new unit price
   (`rules.price_change`), or reject (`CANCELLED` by the seller). Approve → `CONFIRM_SENT` by the seller.
4. The customer's `message_in` (quick reply `CONFIRM:<order_id>` / `CANCEL:<order_id>`, or a plain yes in the
   thread) → `CONFIRMED` with a receipt `{card_id, card_version, image_url, price}` from that card version, and a
   receipt message that also carries `sku` and `name`; or `CANCELLED` by the customer. If the card got a new version after the confirmation was sent
   (after `CONFIRM_SENT`'s `ts_ms`) and that version has another price, another SKU or no longer lists the line's variant, the
   yes goes to `NEEDS_SELLER` instead (`flow.card_change`). The order keeps its pinned version and price. The
   reason names the version at the send and the version at the yes, and `caused_by` adds the newer card event. A
   card change between the comment and the send does not count: the order keeps the locked version and price. A
   new name or image after the send is not a change either: the receipt names the quoted version and its image.

## Card version lock (`flow.locked_card`)

The worker adds the cut-out image in a later card version, after the price. An order is locked when it is drafted
(`draft_order`, used by both order flows):

- to the version that was live at the comment's `ts_ms`;
- or, when the versions after it that are already known at drafting change only `name` or `image_url` (same SKU,
  price, currency, variants and stock, nothing dropped), to the last of them. The walk stops at the first version
  that changes anything else, so a new price, SKU or variant list keeps the comment-time version, as before.
  The DRAFT's notes say which version was taken and `caused_by` adds that card event.

A locked version without a SKU goes to the seller (`NEEDS_SELLER`, "has no SKU"); one without a price goes the
seller's way as before (`rules.py` is unchanged). One without an image is allowed: the receipt has `image_url:
null`, and the C5 scorer counts receipts with an image (`with_image`). An image that arrives after the quote does
not reach that order's receipt; on camera, wait for the image on the card before customers order.

## Cancel

1. `intent.cancel_request(text)` reads a comment labelled `other` or `order` (before any draft, so "huỷ đơn đôi 39"
   never makes a new order). Negation ("đừng huỷ", "không huỷ đâu", "khỏi huỷ"), a link, "bị/chưa huỷ" or a hedge
   ("hay", "có nên") is not a cancel: with label `other` nothing is sent, with label `order` the draft goes on as
   usual ("chốt 1 đôi size 39, ko huỷ nha"). Scam and spam labels are never read.
2. A cancel request, or a plain "huỷ"/"không" in Messenger, asks the same customer (`user_ref` → thread) about their
   open orders (not `CLOSED`/`CANCELLED`) with an `ask_cancel` message, if `rules.can_message` allows it (24 h):
   - one open order → names it, quick replies `Huỷ đơn` → `CANCEL:<order_id>`, `Giữ đơn` → `KEEP:<order_id>`;
   - two or more → lists them, one `CANCEL:<order_id>` per order (up to 12) and `Giữ tất cả` → `KEEP:all`.
     No order is picked for the customer;
   - no open order → nothing is sent.
3. Only `CANCEL:<order_id>` from that order's own thread moves it to `CANCELLED` (actor `customer`), with an
   `order_cancelled` message. A second `CANCEL`, or one on a terminal order, changes nothing. `KEEP` changes
   nothing. An order in `NEEDS_SELLER` may be cancelled by the customer; the approval queue and prices are not
   touched.

## Ask for the missing size or color (`variants.py`)

1. "lấy đôi này" on a card with sizes 38/39/40 names no size. No order event is made. The same customer
   (`user_ref` → thread, `rules.can_message` 24 h) gets an `ask_variant` message (private reply to the comment)
   listing the variants of the card version the comment was priced from that still have stock, with quick replies
   `PICK:<order_id>#<i>` (`i` indexes that version's variants; order ids never contain `#`). Only the missing part
   is asked (size, color, or both). Nothing is taken from a model, from "như bạn trên" or from another customer's
   order.
2. A `PICK` from that thread, or text that plainly names exactly one of the listed variants by its missing part,
   drafts the order at that version's price. Plain means whole words, the value right after its word ("size 39",
   "cỡ 40", "màu đen") or a reply that is little more than the value ("Lấy 40 nha", "38"), no refusal
   ("không lấy size 40", "ko lấy 39") and no second listed value. Then the usual rules apply:
   stock held by other orders, approval gates, `CONFIRM_SENT` + `confirm_order` (sent by PSID, since the comment
   already had its private reply) or `NEEDS_SELLER` + `waiting_for_shop`.
3. A size not on the card, a bogus pick, "ok", a refusal, a number that is an address or a question ("giao 40
   Lê Lợi", "39 còn không") or other unclear text asks again, by PSID, up to 3 questions in all; no order is made. Text never picks when the thread has two open questions. A cancel phrase ("thôi không lấy
   nữa") ends it with no message. Another customer's reply is ignored.
4. If the customer cannot be messaged (24 h) or no listed variant has stock left, nothing is asked: the order goes
   to the seller (`NEEDS_SELLER`) as before.

Every step is an `order` event whose `caused_by` points at the comment, the card version, the previous order event
and the customer message (and the `ask_variant` question when there was one). `GET /api/sessions/{sid}/orders/{order_id}/log` returns that chain for the console (C6).

## Replay: `data/sessions/ask-variant`

`make replay SESSION=ask-variant` (with fixtures, not `--no-fixtures`) plays c019 "lấy đôi này" and c106 "size như
bạn trên nha shop" with hand-written `order` labels, plus replies: another customer's "size 39" and `PICK` for
c019 (nothing happens), an address (asked again), "size 39" (draft, `confirm_order`), the customer's own
`CONFIRM` (receipt), and "size L" for c106 after another customer took M. `gold.json` lists the final state of each
order and the messages per thread; `tests/test_replay_ask_variant.py` checks them in process. The `fx-` cards keep
the labels worker off these comments, so no model is called.

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
