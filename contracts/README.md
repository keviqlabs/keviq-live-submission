# contracts/ — the shared contract (v1)

Source of truth for every stream. Code reads these files; tests fail if code and contract drift.
Changes go through the contracts owner **and** the Founder, get a `docs/DECISIONS.md` entry, and bump `v` when breaking.

| File | What |
|---|---|
| `events/<type>.schema.json` | JSON Schema (draft 2020-12) per event type; `events/envelope.schema.json` = fields every event has |
| `order_states.json` / `order_states.md` | order state machine |
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
| `order` | `order_id`, `customer_ref`, `lines[]`, `amount`, `state`, `prev_state`, `actor`, `approval_needed`, `reason`, `receipt{card_version, image_url, price}` | orders |
| `message_out` / `message_in` | `thread_ref`, `template`, `payload` | connectors (sim/facebook) |
| `model_call` | `model`, `purpose`, `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`, `ok`, `error`, `units` | meta/ |

## Card versions

- Every edit (person or model) emits a new `card` event with `version + 1`; versions are never overwritten.
- `(card_id, version)` is unique in a session. The store refuses a `card` whose version that card already has under another `event_id`; `POST /api/sessions/{id}/events` answers 409 (the same `event_id` again is still `duplicate: true`). After a 409 a writer never reuses the number: it reads the card's latest version (the highest `version`) and applies its change on top of it, or drops it. In these two rules and in the price route below, "latest version" means the highest `version`.
- `POST /api/sessions/{id}/events` also refuses (409) a `card` whose `price` differs from the card's latest non-null price. A card without a price yet can get one there (D-023); a priced card changes price only through the console route below, which writes through the store.
- An order always pins `card_id + card_version` at the moment it is drafted.
- The order price is the price of the card **live at the comment's `ts_ms`** — never a price taken from the customer's words.
- "Live at t" = the latest version with `ts_ms ≤ t` of each card, if that version's status is `live` or `edited`. `GET /api/sessions/{id}/cards/live?ts_ms=t` returns them, newest first.
- Speech never changes the price of a card that already has one (D-023). A spoken price that differs from the card's price emits `price_proposal` instead; the worker emits none when they are equal. A proposal's `ts_ms` is the time of the speech. A proposal is open while the card is not `retired`, the card's latest price equals its `current_price`, and no `edited_by: "seller"` version of the card is newer than its `card_version`; there is no dismiss event. Proposals are shown in the console only, never on the overlay or to customers.
- A seller price comes only from the console route `POST /api/sessions/{id}/cards/{card_id}/price`, body `{"price": int, "card_version": int, "proposal_event_id": str | null}`. Checks, in this order:
  - 422 `price` not an integer in 1000…100 000 000 (booleans refused); 404 unknown card; 422 `proposal_event_id` is not a `price_proposal` of this card; 409 card `retired`;
  - `price` equal to the latest price → 200 `{"changed": false}`, no event, whatever `card_version` says (a repeated tap after a 200 is safe);
  - 409 `card_version` is not the latest version (the console reloads);
  - otherwise writes a `card` with the latest `version + 1`, the new `price`, status `edited`, `provenance.edited_by: "seller"` (other fields copied from the latest version), `caused_by: [proposal_event_id]` when one is given, `ts_ms` = time of the request; 200 `{"changed": true, "version": n}`. If the store refuses that version because another writer took it first, 409 as above.

## Money

All amounts are integer VND.
