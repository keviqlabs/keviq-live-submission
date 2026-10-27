# Technical decisions during the hackathon

Newest on top. Contract changes must have an entry here.

## 2026-10-27 — first real calls, input format

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
