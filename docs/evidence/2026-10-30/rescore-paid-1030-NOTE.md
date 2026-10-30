# Paid bound rescore, stages 1 and 2 — rescore-paid-1030-2243 and rescore-paid-1030-2248

Both paid stages of the plan in `../2026-10-29/rescore-paid-1030-PLAN.md` (sha256 first 16 `1ac69de2aec057c7`), run on
main at `062dade` (tree `229eb21`), the first paid run since the worker's frame lane was repaired (#65). Same set-up as
the 29 October stage: the C5 scenario, one loop per stage, against the bound trial app `measure.c5_bind:app` on
`127.0.0.1:8040` with the real model key, ledger database `keviq_paid_test`, session cap 8 USD / warning 6 USD, the
rule (d) watcher and the image guard of the plan. Stage 1 is capped at 120 s and stage 2 at 350 s; the plan's condition
for stage 2 (at least one `covered=True` line in the app log or one `comment_attachment` that is not `expired` in
stage 1) was met with 9 and 7 of 10, and stage 2 started about five minutes after stage 1 ended. Neither stage was
stopped: runner rc 0 twice, the watcher never fired, no watchdog, no orphan process, port 8040 closed after each stage.

Against 29 October (`../2026-10-29/rescore-paid-1029-2333-NOTE.md`): every frame was read (120 of 120 and 350 of 350
frames have a `product_seen` read, against 34 of 94), the "missing read" class is empty in both stages (29 October: 10
of 10), and 7 of 10 and 23 of 30 order comments reached `ready`. The remaining expirations are of two kinds, both
findings about the product on this tree rather than defects of the evidence: the first order comment of each loop
arrives before any card exists (`unresolved`), and for a few comments the worker's readiness post reaches the app after
the 4 s deadline although the frame reads it depends on had finished 1.0 to 2.2 s before it (`timing`, detailed below).

| Item | Stage 1 — `rescore-paid-1030-2243` | Stage 2 — `rescore-paid-1030-2248` |
| --- | --- | --- |
| Model calls | 174 `model_call` rows, `SUM(cost_usd)` = **0.26572490 USD** (wrapper query below); `/api/cost` after the stage: `session_usd 0.265725`, `today_usd 0.265725`, `total_usd 0.390594`; `test_sessions` 0.0 | 491 rows, **0.75456405 USD**; `/api/cost` after the stage: `session_usd 0.754564`, `today_usd 1.020289`, `total_usd 1.145158`; `test_sessions` 0.0 |
| Start / stop | app up 22:43:30 ICT, loop `t0 1793375012521` (22:43:32.521), runner rc 0, app `Finished server process` 22:46:29, `wrapper rc=0` 22:46:30 | app up 22:48:51, loop `t0 1793375333131` (22:48:53.131), runner rc 0, app stopped 22:55:40, `wrapper rc=0` 22:55:48 |
| Runner | rc **0**: `loop 1: done, SETTLED/SETTLED behind 0, retired 3, waits {}, failures 0` | rc **0**: `loop 1: done, SETTLED/SETTLED behind 0, retired 10, waits {'many': 3}, failures 0` |
| Watcher (rule d) | did not stop the run; last line before `wrapper rc=0`: `t=174 eval=9 cov=9 bad=0 usd=0.265725 attachments=10 expired=3 [expired:3 ready:7 ]` | did not stop the run; `t=408 eval=26 cov=26 bad=0 usd=0.754564 attachments=30 expired=7 [expired:7 ready:23 ]` |
| Image guard | rc 0, `IMAGE_OK` at 22:44:06: `sam-3.1` cutout `model_call_138921d39524474aab3f` (0.0025 USD), card `card-d42adc7d454f` v4, image sha256 `7b08f438…`, 38573 bytes | rc 0, `IMAGE_OK` at 22:49:27: `sam-3.1` cutout `model_call_ade8796f19d64d9ea2f4` (0.0025 USD), card `card-00889dd9fba6` v5, image sha256 `34bfd130…`, 5023 bytes |
| Media | 120 frames, 23 speech segments, 121.0 s | 350 frames, 54 speech segments, 351.1 s |
| Frames read | 120 of 120 frames have a `product_seen` read; the read starts on arrival (`model_call.ts_ms` = frame `ts_ms`) | 350 of 350 |
| `comment_attachment` | 10 rows: `ready` 7, `expired` 3 | 30 rows: `ready` 23, `expired` 7 |
| Classification (c) | n = 10 order comments with a deadline inside the window: missing read 0, **timing 2**, **unresolved 1**, evidence rejected 0, ready 7, ambiguous 0 | n = 30: missing read 0, **timing 5**, **unresolved 2**, evidence rejected 0, ready 23, ambiguous 0 |
| App log | `attachment evaluation` lines 9, all `covered=True` (`resolved=True` 7, `resolved=False` 2); `POST /attachments/system` 11 (200 ×9, 202 ×2); `401`/`422` on it 0; `422` on `/orders/<id>/decision` 0; 5xx 0 | 26 lines, all `covered=True` (`resolved=True` 23, `resolved=False` 3); `POST /attachments/system` 31 (200 ×28, 202 ×3); 0; 0; 0 |
| Worker | `C5PROC` proof: sha `062dade8…`, catalog `data/comments/c5/catalog.json` sha256 `fd5e86ad…`, 10 items, `model_key_set true`; last progress: delivered 458, finished 458, errors 0, skipped 0, stale 0 | same proof; delivered 1314, finished 1314, errors 0, skipped 0, stale 0 |
| Situations (scorer) | PASS 4 (2, 5, 9, 12), FAIL 3 (1, 3, 4), NOT_TRIGGERED 4 (6, 7, 8, 10), TEST_ONLY 1 (11) | PASS 6 (1, 2, 6, 7, 9, 12), FAIL 3 (3, 4, 5), NOT_TRIGGERED 2 (8, 10), TEST_ONLY 1 (11) |
| Ports / databases | 8000 untouched (the main app's `/api/cost` still reports `today_usd 0.0` after both stages); 8040 answered nothing before the start and is closed after the stop; the `keviq` database untouched; `keviq_paid_test` kept | same; the ledger now holds 742 `model_call` rows in three sessions, 1.14515785 USD in total |

Scorer detail of the FAIL rows (full text in `rescore-paid-1030-<run>-scenarios.json`): stage 1 situation 1, items with
a card per loop 3 of 10 (stage 2: 10 of 10, PASS); situation 3, card accuracy 0.5556 on n = 9 and 0.3571 on n = 28
against a target of 0.9 on at least 50 (no card 4 and 18, previous card 1 of 2 and 1 of 4); situation 4, stock questions
answered 0 of 7 and 0 of 13; situation 5 (stage 2 only), late comments are not tied to the card shown when they were
written. Situations 8 and 10 did not trigger in 350 s. These are findings about the product on this tree; nothing in
this note changes them.

## Wrapper output (verbatim, `rescore-paid-1030-2243-wrapper.log`)

```
DATABASE_URL name: /keviq_paid_test
MODEL_API_KEY set: yes
binding: session rescore-paid-1030-2243 epoch epoch-rescore-paid-1030-2243 (two tokens generated, not shown)
migrations applied: none (up to date)
healthz: {"ok":true,"store":"pg","head_seq":339}
cost before: {"today_usd":0.0,"total_usd":0.124869,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.0,"session_cap_usd":8.0,"session_warn_usd":6.0}
cost pre-flight ok
runner rc=0
guard rc=0
{"t": "22:44:06", "kind": "IMAGE_OK", "cutout_call": "model_call_138921d39524474aab3f", "card_id": "card-d42adc7d454f", "version": 4, "event_id": "card_a537d3042b4b41778e86", "image_sha256": "7b08f438a38fff45a97a7a0009cb6b5b8ed0193814a3232a6088a9959ad6fd68", "bytes": 38573}
session cap 8.0 USD, warning 6.0 USD (env)
loop 1: t0 1793375012521
loop 1: done, SETTLED/SETTLED behind 0, retired 3, waits {}, failures 0
run rescore-paid-1030-2243 sha 062dade888838151e87975727ac293bd9dee6db7 loops 1
SELECT session_id, count(*) AS n, COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd
FROM events WHERE type = 'model_call' AND session_id = 'rescore-paid-1030-2243' GROUP BY session_id;
session_id | n | usd
rescore-paid-1030-2243 | 174 | 0.26572490
(1 rows)
cost after: {"today_usd":0.265725,"total_usd":0.390594,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.265725,"session_cap_usd":8.0,"session_warn_usd":6.0}
state after: {'attachment_enabled': True}
app log: attachments INFO lines 9, covered=True 9
app stopped
```

## Wrapper output (verbatim, `rescore-paid-1030-2248-wrapper.log`)

```
DATABASE_URL name: /keviq_paid_test
MODEL_API_KEY set: yes
binding: session rescore-paid-1030-2248 epoch epoch-rescore-paid-1030-2248 (two tokens generated, not shown)
migrations applied: none (up to date)
healthz: {"ok":true,"store":"pg","head_seq":801}
cost before: {"today_usd":0.265725,"total_usd":0.390594,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.0,"session_cap_usd":8.0,"session_warn_usd":6.0}
cost pre-flight ok
runner rc=0
guard rc=0
{"t": "22:49:27", "kind": "IMAGE_OK", "cutout_call": "model_call_ade8796f19d64d9ea2f4", "card_id": "card-00889dd9fba6", "version": 5, "event_id": "card_9d2d02bd78d34f5ca872", "image_sha256": "34bfd130e960e93309d4882789765eaac3f5d448e835a58e32a13803007c1058", "bytes": 5023}
session cap 8.0 USD, warning 6.0 USD (env)
loop 1: t0 1793375333131
loop 1: done, SETTLED/SETTLED behind 0, retired 10, waits {'many': 3}, failures 0
run rescore-paid-1030-2248 sha 062dade888838151e87975727ac293bd9dee6db7 loops 1
SELECT session_id, count(*) AS n, COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd
FROM events WHERE type = 'model_call' AND session_id = 'rescore-paid-1030-2248' GROUP BY session_id;
session_id | n | usd
rescore-paid-1030-2248 | 491 | 0.75456405
(1 rows)
cost after: {"today_usd":1.020289,"total_usd":1.145158,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.754564,"session_cap_usd":8.0,"session_warn_usd":6.0}
state after: {'attachment_enabled': True}
app log: attachments INFO lines 26, covered=True 26
app stopped
```

## Order comments and the classification (c)

Method, the same for both stages. `R(C)` is the set of frames with `ts_ms` at or before the comment's `ts_ms`; a frame
counts as read when a `product_seen` `model_call` names it in `caused_by` (a JSON array, so the join is
`body->'caused_by' ? frame_id`). The app's `attachment evaluation` lines carry no comment id; each line is attributed to
the comment whose `deadline_ms` equals the line's wall-clock millisecond plus its `remaining_ms` (exact to 0 or 1 ms in
every case, the second-best candidate at least 1.1 s away), hence ambiguous 0. Classes follow the plan's rule (c) in
order: missing read (a frame of `R(C)` skipped, stale or still running at the deadline), timing (an evaluation line
with `covered=True resolved=True` that still expired, or no evaluation line before the deadline while the last required
frame's read had finished inside the window), unresolved (`resolved=False`), evidence rejected (`covered=False`), ready.
`deadline_ms` is `comment_ingested_ms + 4000` on every row. Two comments are written long before they are ingested
(`c5-1-024` 12.1 s, and in stage 2 also `c5-1-035` 17.0 s): the scenario's late comments, counted from ingestion.

### Stage 1 — `rescore-paid-1030-2243` (t0 1793375012521)

| comment | ingested − t0 (ms) | deadline − t0 | outcome (reason) | `R(C)` | of them read | candidates | card | class |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| c5-1-001 | 3471 | 7471 | expired (original) | 4 | 4 | 0 | – | unresolved |
| c5-1-006 | 27460 | 31460 | expired (recovery-late) | 28 | 28 | 1 | – | timing |
| c5-1-010 | 40781 | 44781 | ready | 41 | 41 | 1 | card-d42adc7d454f | ready |
| c5-1-016 | 71112 | 75112 | ready | 72 | 72 | 2 | card-26d7314f11f7 | ready |
| c5-1-018 | 76878 | 80878 | ready | 77 | 77 | 2 | card-d42adc7d454f | ready |
| c5-1-021 | 80110 | 84110 | ready | 81 | 81 | 2 | card-26d7314f11f7 | ready |
| c5-1-024 | 97912 | 101912 | ready | 86 | 86 | 2 | card-26d7314f11f7 | ready |
| c5-1-029 | 103623 | 107623 | ready | 104 | 104 | 2 | card-8057d0521e8c | ready |
| c5-1-031 | 107563 | 111563 | ready | 108 | 108 | 2 | card-3d105cde69f5 | ready |
| c5-1-032 | 109011 | 113011 | expired (recovery-late) | 110 | 110 | 2 | – | timing |

The nine evaluation lines and their comments: 22:43:39.562 `resolved=False remaining_ms=430` and 22:43:39.809
`resolved=False remaining_ms=183` → `c5-1-001`; 22:44:15.732 (1570) → `c5-1-010`; 22:44:46.114 (1519) → `c5-1-016`;
22:44:52.326 (1073) → `c5-1-018`; 22:44:54.784 (1847) → `c5-1-021`; 22:45:13.072 (1361) → `c5-1-024`; 22:45:19.176
(968) → `c5-1-029`; 22:45:24.019 (65) → `c5-1-031`; the seven in parentheses are `resolved=True`.

### Stage 2 — `rescore-paid-1030-2248` (t0 1793375333131)

| comment | ingested − t0 (ms) | deadline − t0 | outcome (reason) | `R(C)` | of them read | candidates | card | class |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| c5-1-001 | 3468 | 7468 | expired (original) | 4 | 4 | 0 | – | unresolved |
| c5-1-006 | 27461 | 31461 | expired (recovery-late) | 28 | 28 | 1 | – | timing |
| c5-1-010 | 40769 | 44769 | ready | 41 | 41 | 1 | card-00889dd9fba6 | ready |
| c5-1-016 | 71108 | 75108 | ready | 72 | 72 | 2 | card-e74ac477231d | ready |
| c5-1-018 | 76862 | 80862 | ready | 77 | 77 | 2 | card-00889dd9fba6 | ready |
| c5-1-021 | 80110 | 84110 | ready | 81 | 81 | 2 | card-e74ac477231d | ready |
| c5-1-024 | 97914 | 101914 | ready | 86 | 86 | 2 | card-e74ac477231d | ready |
| c5-1-029 | 103610 | 107610 | ready | 104 | 104 | 2 | card-0e6ec0abec44 | ready |
| c5-1-031 | 107558 | 111558 | ready | 108 | 108 | 2 | card-a867757fa4d7 | ready |
| c5-1-032 | 109010 | 113010 | expired (original) | 110 | 110 | 2 | – | timing |
| c5-1-035 | 129905 | 133905 | ready | 113 | 113 | 2 | card-0e6ec0abec44 | ready |
| c5-1-036 | 120853 | 124853 | expired (original) | 121 | 121 | 2 | – | unresolved |
| c5-1-041 | 136131 | 140131 | ready | 137 | 137 | 2 | card-9a53633c60f5 | ready |
| c5-1-043 | 142582 | 146582 | ready | 143 | 143 | 2 | card-9a53633c60f5 | ready |
| c5-1-045 | 147061 | 151061 | ready | 148 | 148 | 2 | card-0e6ec0abec44 | ready |
| c5-1-047 | 149048 | 153048 | ready | 150 | 150 | 2 | card-9a53633c60f5 | ready |
| c5-1-048 | 155483 | 159483 | ready | 156 | 156 | 2 | card-9a53633c60f5 | ready |
| c5-1-054 | 178699 | 182699 | ready | 179 | 179 | 2 | card-99c094b8da42 | ready |
| c5-1-055 | 180306 | 184306 | ready | 181 | 181 | 2 | card-e23df93dcded | ready |
| c5-1-056 | 181451 | 185451 | expired (recovery-late) | 182 | 182 | 2 | – | timing |
| c5-1-060 | 200869 | 204869 | ready | 201 | 201 | 2 | card-cd943c6f877b | ready |
| c5-1-062 | 207560 | 211560 | ready | 208 | 208 | 2 | card-cd943c6f877b | ready |
| c5-1-071 | 235529 | 239529 | ready | 236 | 236 | 2 | card-821bebc7bf49 | ready |
| c5-1-074 | 240314 | 244314 | ready | 241 | 241 | 2 | card-821bebc7bf49 | ready |
| c5-1-078 | 258624 | 262624 | ready | 259 | 259 | 2 | card-209b8801034c | ready |
| c5-1-082 | 277084 | 281084 | ready | 278 | 278 | 2 | card-e83848072e11 | ready |
| c5-1-085 | 286270 | 290270 | expired (original) | 287 | 287 | 2 | – | timing |
| c5-1-091 | 320058 | 324058 | ready | 321 | 321 | 2 | card-3dedf4c71ee8 | ready |
| c5-1-095 | 335883 | 339883 | expired (original) | 336 | 336 | 2 | – | timing |
| c5-1-098 | 339921 | 343921 | ready | 340 | 340 | 2 | card-e3d8dd9822a0 | ready |

The 26 evaluation lines: 22:49:00.168 (`resolved=False`, 432) and 22:49:00.379 (`resolved=False`, 220) → `c5-1-001`;
22:50:57.562 (`resolved=False`, 422) → `c5-1-036`; the 23 `resolved=True` lines map one to one onto the 23 `ready`
comments. The `POST /attachments/system` answered 202 for the three `resolved=False` posts and 200 for the rest.

### The unresolved and timing rows

Unresolved: `c5-1-001` is the first order comment of the loop in both stages; its four frames were read (the last read
finished 1.1 s and 1.4 s before the deadline), the evaluation ran twice inside the window, but with 0 candidates: the
first `card` of the loop exists only at t0 + 19.3 s (first `speech` at t0 + 2.9 s and 3.0 s, first `product_seen` at
t0 + 17.0 s in both stages). `c5-1-036` (stage 2) was evaluated once, 422 ms before its deadline, with two candidates
and `resolved=False`.

Timing: no evaluation line exists before the deadline, yet the read of the last frame of `R(C)` — and even the read of
the next frame — had finished before it; the worker's post for the comment reached the app only after the deadline
(`snapshot_at_ms` of the `expired` row, equal to the time of the matching `POST /attachments/system` in the app log).
Gap = `emitted_ms` of the last required read minus `deadline_ms`; read length = that call's `latency_ms`.

| stage | comment | last required read − deadline (ms) | read length (ms) | next frame's read − deadline (ms) | worker's post − deadline (ms) |
| --- | --- | --- | --- | --- | --- |
| 1 | c5-1-006 | −1747 | 2367 | −1076 | +1437 |
| 1 | c5-1-032 | −2085 | 1706 | −1161 | +831 |
| 2 | c5-1-006 | −2238 | 1992 | −908 | +241 |
| 2 | c5-1-032 | −1994 | 1791 | −134 | +73 |
| 2 | c5-1-056 | −1509 | 2683 | −1195 | +779 |
| 2 | c5-1-085 | −1008 | 3002 | −950 | +32 |
| 2 | c5-1-095 | −2194 | 2453 | −1712 | +48 |

Stage 1: gap median −1916 ms, nearest −1747 ms; read length median 2037 ms, max 2367 ms; the post arrived 831 and
1437 ms late. Stage 2: gap median −1994 ms, nearest −1008 ms; read length median 2453 ms, max 3002 ms; the post arrived
32 to 779 ms late (median 73 ms). So the vision lane is no longer the limit (29 October: 10 of 10 missing reads); the
step between the last read and the readiness post is, by 0.03 to 1.4 s. For every frame of both stages
`model_call.ts_ms − frame.ingested_ms` is about −230 ms, that is the reads start on arrival and run concurrently.

## Model calls (ledger, `keviq_paid_test`)

The session totals are in the wrapper output above (the wrapper's `SUM` query). By purpose and model, with
`count`, `SUM(cost_usd)`, average and maximum `latency_ms`; every row of both sessions has `ok = true`:

| purpose | model | stage 1 calls | USD | avg ms | max ms | stage 2 calls | USD | avg ms | max ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `product_seen` | `muse-spark-1.3` | 120 | 0.16367405 | 2144 | 3563 | 350 | 0.46860350 | 2134 | 3905 |
| `card_from_speech` | `muse-spark-1.3` | 21 | 0.05086800 | 3014 | 4029 | 52 | 0.13232595 | 3065 | 6711 |
| `label_comments` | `muse-spark-1.3` | 29 | 0.03768285 | 1926 | 3376 | 78 | 0.11113460 | 1928 | 4414 |
| `card_cutout` | `sam-3.1` | 3 | 0.0075 | 1514 | 1752 | 10 | 0.0250 | 1129 | 2123 |
| `transcribe` | `muse-voice-transcribe-1.0` | 1 | 0.006 | 120895 | 120895 | 1 | 0.0175 | 350936 | 350936 |

`ok` and the ledger across sessions:

```
rescore-paid-1029-2333 | 77 | 76 | 0.12486890
rescore-paid-1030-2243 | 174 | 174 | 0.26572490
rescore-paid-1030-2248 | 491 | 491 | 0.75456405
all sessions | 742 | 741 | 1.14515785
```

Events by type (stage 1 | stage 2):

| type | stage 1 | stage 2 |
| --- | --- | --- |
| `model_call` | 174 | 491 |
| `frame` | 120 | 350 |
| `comment_labeled` | 44 | 120 |
| `comment` | 34 | 100 |
| `card` | 21 | 92 |
| `speech` | 23 | 54 |
| `product_seen` | 7 | 38 |
| `comment_attachment` | 10 | 30 |
| `order` | 14 | 22 |
| `message_out` | 9 | 14 |
| `price_proposal` | 1 | 2 |
| `message_in` | 1 | 1 |

The `model_call` rows of each session are exported to `rescore-paid-1030-<run>-model-calls.jsonl` (one row per line,
the `transcribe` row is the single call that spans the whole loop: 120.9 s and 350.9 s). The provider's billing page was
not read for this note; the costs above are the ledger's.

## Files beside this note (sha256, first 16)

| file | sha256 | bytes |
| --- | --- | --- |
| `rescore-paid-1030-2243-app.log` | `094f37086922790a` | 143794 |
| `rescore-paid-1030-2243-comments.jsonl` | `b22db9cefbf784e3` | 12004 |
| `rescore-paid-1030-2243-image-guard.log` | `f35ef0aec7b5ba3f` | 725 |
| `rescore-paid-1030-2243-loop1-media.log` | `bfbca39deda9d0b9` | 20817 |
| `rescore-paid-1030-2243-loop1-worker.log` | `64793ee5f094cd7c` | 285602 |
| `rescore-paid-1030-2243-model-calls.jsonl` | `f7740f7dd784ff5c` | 106591 |
| `rescore-paid-1030-2243-runner.log` | `c17a088890944b59` | 678 |
| `rescore-paid-1030-2243-scenarios.json` | `d498ccd133b3e4bc` | 3442 |
| `rescore-paid-1030-2243-watch.log` | `b9836f439183c747` | 1696 |
| `rescore-paid-1030-2243-wrapper.log` | `9baaa4f34b24fe21` | 1688 |
| `rescore-paid-1030-2243.json` | `6d836ef9e45592e4` | 26458 |
| `rescore-paid-1030-2248-app.log` | `7479f6f70103a671` | 388874 |
| `rescore-paid-1030-2248-comments.jsonl` | `fbd9a66c4dae388c` | 34535 |
| `rescore-paid-1030-2248-image-guard.log` | `9a411e4232007eee` | 723 |
| `rescore-paid-1030-2248-loop1-media.log` | `ea138d6041322d4f` | 56574 |
| `rescore-paid-1030-2248-loop1-worker.log` | `176c6fb23960eb53` | 781220 |
| `rescore-paid-1030-2248-model-calls.jsonl` | `45c935a954df380c` | 301322 |
| `rescore-paid-1030-2248-runner.log` | `5c8160c0d748f0db` | 688 |
| `rescore-paid-1030-2248-scenarios.json` | `e846c0aee84fbd0f` | 3508 |
| `rescore-paid-1030-2248-watch.log` | `3a9342ae9b282239` | 3924 |
| `rescore-paid-1030-2248-wrapper.log` | `0b2b40eb55cbada1` | 1704 |
| `rescore-paid-1030-2248.json` | `c74a2f1cdf1098f6` | 38805 |

The wrapper, the watcher, the query script and the uvicorn log config are the tracked 29 October files
`../2026-10-29/rescore-paid-run.sh` (`b19004443a8901f0`), `rescore-paid-go-stage.sh` (`ba0d787284230631`),
`rescore-paid-after-stage.sh` (`31af78fea2f68569`) and `rescore-paid-log-config.json` (`24209442aaac4d61`); the copies
used today were byte-identical and are not duplicated here. The wrapper reads the key from the checkout's `.env` and
prints only whether it is set; the files beside this note carry no home or scratch paths.
