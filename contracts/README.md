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
| `comment` | `comment_id`, `user_ref` (pseudonym), `text`, `reply_to` | connectors |
| `comment_labeled` | `comment_id`, `label`, `order_draft{sku, variant, qty, referenced_card_id, reference_kind}`, `scam_signals[]`, `action`, `latency_ms` | worker/brain |
| `order` | `order_id`, `customer_ref`, `lines[]`, `amount`, `state`, `prev_state`, `actor`, `approval_needed`, `reason`, `receipt{card_version, image_url, price}` | orders |
| `message_out` / `message_in` | `thread_ref`, `template`, `payload` | connectors (sim/facebook) |
| `model_call` | `model`, `purpose`, `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`, `ok`, `error`, `units` | meta/ |

## Card versions

- Every edit (person or model) emits a new `card` event with `version + 1`; versions are never overwritten.
- An order always pins `card_id + card_version` at the moment it is drafted.
- The order price is the price of the card **live at the comment's `ts_ms`** — never a price taken from the customer's words.
- "Live at t" = the latest version with `ts_ms ≤ t` of each card, if that version's status is `live` or `edited`. `GET /api/sessions/{id}/cards/live?ts_ms=t` returns them, newest first.

## Money

All amounts are integer VND.
