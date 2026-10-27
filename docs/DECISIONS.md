# Technical decisions during the hackathon

Newest on top. Contract changes must have an entry here.

## 2026-10-27 — live product cards (worker/media, vision, voice, brain/card)

Numbers continue after D-014/D-015 (PR #4) and D-016 (PR #6).

**D-021 Frames, Spark latency, stream wiring.** Frames are cut at 1 fps with the long side at most 640 px (D-015: phone portrait 1080×1920 → 360×640). Spark frame reads with the current `meta/client.py`, measured on synthetic frames: p50 4782 ms / p95 6233 ms (n=20); card_from_speech 5–8 s. `worker/brain/__init__.py` merges each stream's HANDLERS per event type with one extend-safe line, so other streams append without editing card code. Events from `worker/media` (frame, speech) use `source: "replay"` because they come from a recording, and carry the four C8 timing keys on the session clock.

**D-020 The card image is the frame cut to SAM's box with SAM's mask as alpha.** SAM's reply shape is in D-014; `meta.client.parse_segmentation` reads it. The mask is decoded with `meta_sam_parser.decode_mask_to_raster` (Meta's package, pinned at 0.1.0, no runtime dependencies; added on the Founder's order of 27 October). What we found:
- The decoder returns one byte per pixel, 0 or 1 (lossless coverage ≥ 129 counts as 1), row-major with the top row first. `parse_segmentation` drops the payload's `~`/`!` marker, so `worker/vision/sam.py` puts it back before decoding.
- The mask is box-local: `x=0;y=0` and H×W equal the box size (corners inclusive). Meta's own parser accepts only `x=0;y=0`, so we do the same; a mask that is not box-sized, has other x/y, or does not decode leaves the plain box crop (background kept) and a log line.
- The real reply for `meta.probe.test_image()` (200×160 red box on grey 640×480) decodes to 160×200 with 99.9 % foreground in 87 ms; its output text is `tests/fixtures/sam_red_box.txt`.
- One real call on a synthetic portrait frame (1080×1920 scaled to 360×640, a red right triangle with the right angle bottom-left): a 158×158 cut-out, 50.9 % opaque, top row 1/158 opaque and bottom row 156/158, left column 158/158 and right column 3/158. This confirms the row and column order on a real mask. 1151 ms, 0.0025 USD.

The cut-out is the frame's own pixels: the tests check that every RGB value equals the plain box crop's. Only the alpha channel is added, as a PNG data URL at most 360 px wide (the PNG is never upscaled). No box → `image_url` stays null.

**D-019 Voice runs in ENDPOINTING mode; one `speech` event per completed turn.** The handshake and message types are in D-014 (`meta/client.py`, PR #4; this branch does not change the client). `worker/voice` sends `mode: "ENDPOINTING"`, the catalog as `keywords` and `languageBias: ["Vietnamese"]`. Only `speechComplete` becomes a `speech` event; its span is the turn's `speechStart` → `speechComplete` `audioProcessedMs` (processing progress, not word times). An `error` message ends the stream. Real run on 300 s of synthetic Vietnamese speech (macOS TTS, 15 seller lines looped): 67 final turns, final-after-turn-end p50 1422 ms / p95 1504 ms (n=67), 0.015 USD. The 5 turns printed match the script except one doubled word, and one turn joins two lines; word error rate was not measured.

**D-018 Speech is read in the background.** card_from_speech takes 5–8 s on Spark, and the worker handles events one at a time, so an inline read delayed the next `product_seen` card by up to 2.4 s on demo1. `on_speech` now pins the card that is current when the speech arrives and returns at once; the read finishes as a background task and posts its version then. A card with a read in flight is not retired by a product switch; if the read brings no price after the switch, the card is retired then. Cost: a card goes live about 5 s after the price is spoken, against ~0 ms for the rule reader alone.

**D-017 Replayed product_seen needs a flag to open cards.** A `product_seen` without `caused_by` comes from a replay file, not from the vision worker. It opens cards only with `CARDS_FROM_REPLAY=1`, so a replay that still carries fixture cards does not get a second set. With `--no-fixtures` the vision worker makes `product_seen` from frames and sets `caused_by`.

## 2026-10-27 — first real calls, input format

**D-016 Tests and smoke use a separate `_test` database; the test rows already written stay.** Until now `test_pg_store_append_only` and `make smoke` wrote to the project database, and a labels worker running live on that database labeled one test comment for real: one billed Spark call, 0.003589 USD, session `pytest-pg`, 27 October. At 12:34 the project database held 566 rows from test sessions (`pytest-pg` 14, eight `smoke-*` 69 each); the log is append-only, so they stay, and branches without this change keep adding to them until they merge it. From now on:
- pytest and smoke use `TEST_DATABASE_URL`, else `DATABASE_URL` with `_test` added to the database name (`keviq_test`), created and migrated on first use (`python -m app.migrate --test`). pytest exits with code 1 before any test runs if that name does not end in `_test`; smoke and `migrate --test` refuse the same way.
- pytest and smoke run with `MODEL_API_KEY` set to empty, so no test makes a billed call (the voice tests set a fake key of their own).
- `/api/cost` adds `test_sessions` (session ids starting `pytest` or `smoke-`): today's and total spend caused by test sessions. The caps still count every `model_call`, test or not: the 0.003589 USD was billed, and a cap that left it out would sit below the real bill.

**D-015 Phone portrait is the default input.** The live source is a phone stream, 9:16 (1080×1920). The frame cutter, product card and overlay take portrait as the default; landscape from OBS is a later, secondary case. The Founder's test video goes in `data/local/test1/` (portrait). Spark's "640 px" frame means the long side (360×640, same token cost as planned); if product recognition on test1 misses the gate, try 720×1280 and record the cost next to the result.

**D-014 Model API shapes checked with one real call per model (closes D-007).** `python -m meta.probe` on synthetic inputs (an ffmpeg-drawn image, 10 s of macOS speech), 27 October, three `model_call` events in session `probe-20261027`:

| model | tokens in/out | cost USD | latency ms | units |
|---|---|---|---|---|
| muse-spark-1.3 (structured output, reasoning minimal) | 415 / 111 | 0.000991 | 2072 | — |
| sam-3.1 (one image) | 0 / 0 | 0.002500 | 696 | images 1 |
| muse-voice-transcribe-1.0 (ENDPOINTING, 10 s) | 0 / 0 | 0.000500 | 20842 (whole stream) | audio_seconds 10 |

What the replies showed, and what `meta/client.py` now does:
- Spark: `output[]` is a `reasoning` item (no content) then a `message` with `output_text`; usage has `input_tokens_details.cached_tokens` and `output_tokens_details.reasoning_tokens`. Reasoning tokens are inside `output_tokens` and billed. `reasoning.effort` is a parameter of `responses()`.
- SAM 3.1: input is a `message` with `input_text` + `input_image`, `stream: false`, metadata `include_confidence`, `mask_encoding`. The reply is text in the `output_text` lane, not images: `<0f>0<|box;x1;y1;x2;y2;w;h;c|><|mask;x;y;c;data=H,W,~payload|>`. Box corners are inclusive pixels, box w/h are the image size, mask H×W is the box size. `usage.images_processed` is present. `parse_segmentation()` returns the records; mask pixels are not decoded in `meta/` (the reference decoder is `meta_sam_parser`, not yet a dependency; decoding is unverified).
- Voice: the key goes in the first JSON frame (`authorization.accessToken`), not a header; `audioEncoding` PCM_24KHZ; `languageBias` takes names (`Vietnamese`), not codes; audio frames of 80 ms paced at real time, then `{"type": "endStream"}`. Sequence seen: `{sessionId}`, `speechStart`, 24 cumulative `transcript` partials, `speechEnd` and `speechComplete` (turn transcript, at 8140 ms of audio), then an empty `transcript` with `final: true` at end of stream. The latency above is the whole session (10 s of real-time audio plus close), not speech-to-text delay.

Tests: `tests/test_meta_shapes.py` replays these shapes offline (fail on the previous client, pass now).

## 2026-10-27 — scaffold + contracts

**D-013 Acceptance criteria in English (v1).** `contracts/acceptance.md` is replaced by the Founder's English version of C1–C11 (27 October 2026); the Vietnamese copy of the day-1 table is retired. Changes in form: the "changed from v0.2" column becomes "how it is checked", the out-of-P0 row becomes a paragraph, and the C11 sourcing note is shortened. The criteria and targets are the same; no schema, measure or test change follows from it.

**D-012 Rules boundary with the orders stream.** `orders/rules.py` holds the permission table (mirrors `contracts/permissions.json`, a test checks they are equal), `authorize()`, the 24 h message window and `evaluate_draft()`. The orders stream extends `evaluate_draft()` and keeps the `Decision(allowed, needs_seller, reason)` shape; any change to `rules.py` comes with a test in `tests/test_rules_invariants.py`.

**D-011 Labeled comment sets not created yet.** The 200 and 500 labeled comments for C3/C5 (`data/comments/`) are not in the scaffold. Today `measure/` runs on demo1 (14 comments, 7 orders); its C3 line prints the target n=50 next to the real n so the gap stays visible.

**D-010 C8 display stage is approximate.** `display = ingested_ms - timing.emitted_ms` (worker emit to app store). Console render time is not measured yet; the README must say so next to any published C8 number.

**D-009 C1 interpretation.** Consequence of trigger T = an event listing T in `caused_by` whose arrival time is within 0..5000 ms of T's arrival (`when()` = `ingested_ms`, else `_replay_at_ms`, else `ts_ms`). Triggers: every `product_seen`; every `comment_labeled` whose label is order, price_question, stock_question or complaint. A label with action hide or warn is its own consequence; `other` with action none needs none.

**D-008 Replay file conventions.** `ts_ms` (and `frame_ts_ms`, `start_ms`, `end_ms`) in a session file are relative; replay rebases them to `start + rel / speed` and namespaces every `event_id` (and `caused_by`, `speech_event_ids`) as `<target>.<id>` so one file can be replayed many times. Optional `_replay_at_ms` = arrival time for late comments; stripped before posting. Event ids starting `fx-` are hand-written stand-ins for worker output; `--no-fixtures` drops them so the real worker produces those events. Derived event `ts_ms`: card = version emit time, comment_labeled = the comment's time, order = transition time.

**D-007 Model API shapes not verified against the live API.** Base URL, model names, auth header and prices are from dev.meta.ai docs (read 2026-10-27). The structured-output request/response shape, SAM input shape and the voice WebSocket protocol are written from the docs but not yet called for real, so parsing is defensive (`output_text` or `output[].content[]`, two token-count spellings). First real call per model: check the shape, fix `meta/client.py`, add an entry here.

**D-006 Cost cap is enforced in our code.** The Billing page limit only notifies. `Meter.check()` runs before every call: today (VN day) ≥ 30 USD or total ≥ 300 USD → `BudgetExceeded`, a `model_call` with `ok=false, error=daily_cap|total_cap` is logged, no request is sent. At 20 USD/day a warning tells the Founder. Spend is summed from `model_call` events in PostgreSQL (`/api/cost`). Muse Wonder is refused in code.

**D-005 Local PostgreSQL on 127.0.0.1:55433, bridge network.** Port 55432 was taken on the dev machine and Docker's address pools were exhausted, so compose uses `network_mode: bridge` instead of a project network. Override with `DATABASE_URL` in `.env`.

**D-004 `seq` has gaps.** `seq` is a global BIGSERIAL and the SSE cursor; `INSERT … ON CONFLICT DO NOTHING` consumes a value on duplicates. Consumers must treat `seq` as ordered, not contiguous.

**D-003 One writer.** The app is the only process that writes events. Replay, worker and connectors are HTTP clients (`POST /api/sessions/{id}/events`, SSE `GET /api/events?cursor=`). Ingest is idempotent on `event_id`; the events table is append-only (trigger rejects UPDATE/DELETE).

**D-002 Envelope additions.** Beyond `event_id, session_id, ts_ms, source, v, type`: optional `caused_by[]` (provenance, C1/C6) and `timing{received_ms, infer_start_ms, infer_end_ms, emitted_ms}` (C8). The server adds `seq` and `ingested_ms` on read; they are table columns, not fields of the event body. Schemas inline the envelope and set `additionalProperties: false`.

**D-001 Per-session keys.** `card_id`, `comment_id` and `order_id` are unique within a session; only `event_id` is globally unique.
