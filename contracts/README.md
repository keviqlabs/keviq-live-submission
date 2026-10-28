# contracts/ — the shared contract (v1)

Source of truth for every stream. Code reads these files; tests fail if code and contract drift.
Changes go through the contracts owner **and** the Founder, get a `docs/DECISIONS.md` entry, and bump `v` when breaking.

| File | What |
|---|---|
| `events/<type>.schema.json` | JSON Schema (draft 2020-12) per event type; `events/envelope.schema.json` = fields every event has |
| `requests/<route>.schema.json` | request-body shape; route semantics and history checks are documented below |
| `order_states.json` / `order_states.md` | order state machine |
| `comment_attachment.md` | proposed pre-order attachment semantics, pending product-owner approval; no runtime producer/route |
| `permissions.json` / `permissions.md` | who decides what (assistant · seller · forbidden) |
| `acceptance.md` | functional criteria C1–C11 |

## Event envelope

Every event has `event_id`, `session_id`, `ts_ms`, `source` (`replay` · `facebook` · `sim`), `v` (= 1), `type`.

- **`ts_ms`** is epoch milliseconds of the *source* moment (frame time, when the comment was posted, when speech was said) — never processing time. Replays rebase recorded offsets onto the replay start.
- **`caused_by`** (optional): `event_id`s this event is a consequence of. Every model output must have a consequence that points back at it (C1); provenance on cards and the decision log (C2, C6) are built from this.
- **`timing`** (optional): `received_ms`, `infer_start_ms`, `infer_end_ms`, `emitted_ms` (epoch ms) for per-stage latency (C8).
- The server adds `seq` (global, monotonic — the SSE cursor) and `ingested_ms`; they are not part of the event body.
- Unknown fields are rejected (`additionalProperties: false`). Adding a field is a contract change.

| Event | Main fields | Emitted by |
|---|---|---|
| `frame` | `jpeg_path` or `jpeg_b64`, `width`, `height` | worker/media |
| `speech` | `text`, `final`, `lang`, `start_ms`, `end_ms` | worker/voice |
| `product_seen` | `category`, `attributes{color,size,material…}`, `confidence`, `is_new`, `frame_ts_ms` | worker/vision |
| `card` | `card_id`, `version`, `sku`, `name`, `price`, `currency`, `variants[]`, `stock`, `image_url`, `status`, `provenance{frame_ts_ms, speech_event_ids[], edited_by}` | worker/brain; console edit → version+1 |
| `price_proposal` | `card_id`, `card_version`, `current_price`, `heard_price`, `speech_event_id`; `caused_by` lists the speech | worker/brain, when speech names a different price for a priced card (console only) |
| `comment` | `comment_id`, `user_ref` (pseudonym), `text`, `reply_to` | connectors |
| `comment_labeled` | `comment_id`, `label`, `order_draft{sku, variant, qty, referenced_card_id, reference_kind}`, `scam_signals[]`, `action`, `latency_ms` | worker/brain |
| `comment_attachment` | versioned ready/expired outcome; frozen 0–2 card refs, original clocks/deadline, owner epoch/readiness refs, selected card and server-assigned action/actor | proposed worker-only system ingress/app writer and separate seller route (D-029) |
| `order` | `order_id`, `customer_ref`, `lines[]`, `amount`, `state`, `prev_state`, `actor`, `approval_needed`, `reason`, `receipt{card_version, image_url, price}` | orders |
| `message_out` / `message_in` | `thread_ref`, `template`, `payload` | connectors (sim/facebook) |
| `model_call` | `model`, `purpose`, `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`, `ok`, `error`, `units` | meta/ |

## Card versions

- Every edit (person or model) emits a new `card` event with `version + 1`; versions are never overwritten.
- `(card_id, version)` is unique in a session. The store refuses a `card` whose version that card already has under another `event_id`; `POST /api/sessions/{id}/events` answers 409 (the same `event_id` again is still `duplicate: true`). After a 409 a writer never reuses the number: it reads the card's latest version (the highest `version`) and applies its change on top of it, or drops it. In these two rules and in the price route below, "latest version" means the highest `version`.
- `POST /api/sessions/{id}/events` also refuses (409) a `card` whose `price` differs from the card's latest non-null price. A card without a price yet can get one there (D-023); a priced card changes price only through the console route below, which writes through the store.
- An order always pins `card_id + card_version` at the moment it is drafted.
- The order price is the approved price of the **attached card's latest version when attachment happens**, when that version is live/edited with a price. If it has no approved price, the labeled comment waits without an `order` event for that card's next live/edited priced version, which the order then pins. A `price_proposal`, another card's price or a price in customer text is not an approved price. Existing order pins are not repriced by later versions; no earlier card version is rewritten.
- "Live at t" = the latest version with `ts_ms ≤ t` of each card, if that version's status is `live` or `edited`. This chooses membership, separately from the current-item ordering below.
- Speech never changes the price of a card that already has one (D-023). A spoken price that differs from the card's price emits `price_proposal` instead; the worker emits none when they are equal. A proposal's `ts_ms` is the time of the speech. A proposal is open while the card is not `retired`, the card's latest price equals its `current_price`, and no `edited_by: "seller"` version of the card is newer than its `card_version`; there is no dismiss event. Proposals are shown in the console only, never on the overlay or to customers.
- A seller price comes only from the console route `POST /api/sessions/{id}/cards/{card_id}/price`, body `{"price": int, "card_version": int, "proposal_event_id": str | null}`. Checks, in this order:
  - 422 `price` not an integer in 1000…100 000 000 (booleans refused); 404 unknown card; 422 `proposal_event_id` is not a `price_proposal` of this card; 409 card `retired`;
  - `price` equal to the latest price → 200 `{"changed": false}`, no event, whatever `card_version` says (a repeated tap after a 200 is safe);
  - 409 `card_version` is not the latest version (the console reloads);
  - otherwise writes a `card` with the latest `version + 1`, the new `price`, status `edited`, `provenance.edited_by: "seller"` (other fields copied from the latest version), `caused_by: [proposal_event_id]` when one is given, `ts_ms` = time of the request; 200 `{"changed": true, "version": n}`. If the store refuses that version because another writer took it first, 409 as above.

## Money

All amounts are integer VND.

## Current item and appearance order (D-027; runtime implementation pending)

An appearance is a card version whose non-null `provenance.frame_ts_ms` differs from that card's previous
version, including the first version. Sort appearances by `(frame_ts_ms, version)`. Current at media time t is
the card of the last appearance with `frame_ts_ms ≤ t`; previous is the last earlier appearance of another
card. Card status does not choose identity. A price, size, status or seller SKU edit that keeps frame time is
not a new appearance. A returning item can therefore become current again without a new card ID.

`GET /api/sessions/{id}/cards/live?ts_ms=t` keeps the live-at-t membership above and orders those cards by
their last appearance at or before t, current first when it is live. Only versions written at or before t
are inputs to this replay query. The labeler instead uses all versions known when it attaches a comment;
recognition written after t can therefore change its answer for that same media time. Both use the same rule.
The console list and overlay use appearance order too; a seller price edit cannot move an old item to the top.

Attachment readiness and its bounded wait are defined in `docs/specs/current-item-and-stock-rules.md`.
An expiry snapshots current and previous at the comment's media time using versions known at expiry. With two
known, priced candidates, an order line may carry `candidates: [current_card_id, previous_card_id]` as below.
An expiry state change records the actual expiry decision time, not the earlier comment time.

## Seller chooses an order item (D-027; runtime implementation pending)

`order.lines[].candidates` is optional: exactly two distinct card IDs, current then previous at the comment's
media time. Existing order events without it remain valid v1. Required line fields, prices and order states
are unchanged. Candidates first accompany the draft that immediately goes to `NEEDS_SELLER`; until the seller
chooses, a line's required `card_id`, version and price describe the first candidate and do not authorize a
confirmation or identify a final choice. Those fields must name an actual approved priced version of that
card. They must never be filled with another candidate's price or a placeholder zero.

The candidates are a frozen snapshot. Runtime must check their existence and session, their correspondence to
the attachment snapshot, and the line's card/version/price before writing an order. The order remains in
`NEEDS_SELLER` until the seller chooses or rejects; the customer is not asked which item. Candidates may be
retained on later order versions as decision history. Exactly one candidate-bearing line per order is covered
by the decision body below; choosing among several such lines needs a separate contract decision.

`POST /api/sessions/{id}/orders/{order_id}/decision` body is
`{"decision": "approve" | "reject", "unit_price"?: int | null, "card_id"?: str}`.
The structural schema is `requests/order-decision.schema.json`:

- Existing approve/reject bodies without `card_id` remain valid for an order without candidates. For such an
  order, supplying `card_id` is refused (422); it is not a route to attach an unrelated item.
- Approving a pending candidate choice requires an explicit `card_id` from that line's candidates. Runtime
  checks membership **and the same session** (422 otherwise), that the order is still `NEEDS_SELLER` and that
  exactly one line needs a choice (409 otherwise). Omitting `card_id` must not choose the first candidate.
- Runtime loads the chosen card's latest version at decision time; a successful choice requires that version
  to be live/edited with an approved price. It pins that version and its SKU/variant/price on the new order
  version, recomputes the amount and applies the existing
  quantity, stock, price authority and messaging checks. A line's old price cannot follow it to a different card.
  `unit_price`, if given, uses the existing seller price authority; it grants no assistant discount permission.
- A reject has no `card_id` and follows the existing rejection path. A successful choice records actor `seller`,
  preserves the candidate history and cites the selected card version in `caused_by`. Existing order states
  and customer-confirmation requirements stay in force; choosing an item is not customer confirmation.

Unknown session/order is 404. State, membership, version, price and history checks precede writes and messages;
a refused request writes no order or message. JSON Schema checks syntax only and cannot establish membership
in a stored order, a card's session, the seller's authority, an approved price or a valid state transition.

## Seller identifies an unresolved draft (D-028; runtime implementation pending)

`POST /api/sessions/{id}/cards/{card_id}/item` body is `{"card_version": int, "sku": str | null}`.
`requests/card-item.schema.json` requires both fields. The operation belongs to the seller through the same
console boundary as existing seller price/order decisions; no request-supplied actor can confer authority.
Customer text and automated classification do not authorize the choice. V1 has no separate seller identity;
console access remains the existing local-demo authority boundary.

Runtime checks, atomically with the write:

1. Unknown session/card → 404; card's latest version is not `draft`, or already has a SKU → 409.
2. `card_version` is not the card's latest (highest) version → 409. These checks also apply to `sku: null`.
3. `sku: null` explicitly keeps the card unresolved: 200 `{"changed": false, "version": n}`. This is a no-op:
   no new card event, version, price, order or message; it does not mark frame evidence ready or reset a deadline.
4. A non-null SKU must be an exact member of the session's active catalog (422 otherwise). No guess from price,
   customer text, category or array position is allowed. Runtime uses the same active catalog as the session;
   catalog contents cannot be changed through this route.
5. Append a new `card` event at latest version + 1: selected SKU, catalog name/variants/stock, status still
   `draft`, same card's existing price (including null), currency, image and frame/speech provenance;
   `provenance.edited_by: "seller"`, `caused_by` includes the preceding card event and `ts_ms` is the request's
   time. No old version is mutated. A write race yields 409 without overwriting either writer's event.
6. Return 200 `{"changed": true, "version": n + 1}`. This chooses identity only: it does not copy a previous
   item's price or the catalog price into the draft, promote its status, draft/confirm an order, or send a
   message. If this card lacks an approved price, the separate price path must supply one on a later version.

The existing `card` event can represent the result; no new event type or field is added. Schema tests prove
only the body shape and the existing event shape. Runtime must verify catalog membership, same session,
seller authority, current status, latest-version match, append-only history and unchanged price provenance.
The route does not reassign a card that already has a SKU or change the SKU/version pinned by an existing order.

## Supplemental pre-order proposal, pending product-owner approval

The D-027 gaps (missing price/identity/candidates before an order and persisting ready/expired attachment) have
the separate **proposed** contract in `comment_attachment.md` (D-029). It adds an append-only event and a
pre-order seller-decision request without weakening an order line or adding fields to comment_labeled.
The proposal requires explicit product approval, especially that an empty frozen snapshot cannot be selected
and pre-order rejection only closes the console intent without messaging the customer. It defines a proposed
authenticated session/epoch-bound system ingress with evidence checks, no credentials or runtime implementation,
published full-evidence limits for the approved five-lap run (1755 records, two card refs per frame, 1 MiB),
and the eligibility limit of D-028 for a priced unidentified non-draft. App-owned expiry may have a null worker
epoch, while ready and ingress requests require a real authenticated epoch. Bounds and those product effects
remain proposed for approval; no latency or C5-pass claim follows from the schemas.
The proposal alone does not clear runtime gates or authorize deployment.

These contracts alone do not open the remaining runtime gates. This change implements no route, worker,
order flow or UI; request schemas are not yet wired into runtime validation. V1 is retained because existing
event fields and required shapes remain valid, the event addition is optional, and the new request schemas
are separate from events. Existing deployed validators must be updated before consuming candidate-bearing
orders; an older strict validator will reject the added field.
