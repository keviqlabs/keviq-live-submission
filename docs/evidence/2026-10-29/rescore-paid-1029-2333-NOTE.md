# Paid bound rescore, stage 1 — rescore-paid-1029-2333

The first paid stage of the plan in `rescore-paid-1030-PLAN.md`: the C5 scenario, one loop capped at 120 s, against the
bound trial app `measure.c5_bind:app` on `127.0.0.1:8040` with the real model key, ledger database `keviq_paid_test`,
session cap 8 USD / warning 6 USD. Revision of the branch: `4b7e13f` (the run file that would carry the `C5PROC` sha was
not written, see "What is missing"). The second stage (`--seconds 350`) was **not run**: its condition was at least one
`covered=True` line in the app log or at least one `comment_attachment` that is not `expired`, and the stage ended with
0 and 0/10.

| Item | Value |
| --- | --- |
| Model calls | 77 `model_call` rows for the session, `SUM(cost_usd)` = **0.12486890 USD** (SQL below); `/api/cost` after the run: `session_usd 0.124869`, `today_usd 0.124869`; `test_sessions` 0.0 |
| Start / stop | wrapper start 23:33:58 ICT, loop `t0 1793291640730` (23:34:00.730), runner SIGTERM 23:36:00 by the rule (d) watcher, app `Finished server process` 23:38:25 |
| Runner | rc **143** (SIGTERM from the watcher at t = 122 s: 10 `comment_attachment`, all `expired`, 0 `covered=True`) |
| Image guard | rc 0, `IMAGE_OK` at 23:34:34: `sam-3.1` cutout `model_call_465f12fc6d934a51a069` (0.0025 USD), card `card-34a04b988135` v4, image sha256 `ea39206b…`, 38573 bytes |
| `comment_attachment` | 10 rows, outcome `expired` 10, other outcomes 0 |
| Classification (c) | n = 10 order comments with a deadline inside the window: **missing read 10**, timing 0, unresolved 0, evidence rejected 0, ready 0, ambiguous 0 |
| App log | `attachment evaluation` lines 0, `covered=True` 0, `401`/`422` on `/attachments/system` 0; `422` on `/orders/<id>/decision` 6 (the scripted shop deciding a two-candidate draft; not a stop condition) |
| Ports / databases | 8000 and 8001 untouched; 8040 answered nothing before the start; the `keviq` database untouched; `keviq_paid_test` kept (the `pytest-*` rows stay at 2, from the dry run of 23:19) |

## Wrapper output (verbatim, `rescore-paid-1029-2333-wrapper.log`)

```
DATABASE_URL name: /keviq_paid_test
MODEL_API_KEY set: yes
binding: session rescore-paid-1029-2333 epoch epoch-rescore-paid-1029-2333 (two tokens generated, not shown)
migrations applied: none (up to date)
healthz: {"ok":true,"store":"pg","head_seq":40}
cost before: {"today_usd":0.0,"total_usd":0.0,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.0,"session_cap_usd":8.0,"session_warn_usd":6.0}
cost pre-flight ok
runner rc=143
guard rc=0
{"t": "23:34:34", "kind": "IMAGE_OK", "cutout_call": "model_call_465f12fc6d934a51a069", "card_id": "card-34a04b988135", "version": 4, "event_id": "card_51a31ca3ffc64599a35c", "image_sha256": "ea39206b88b79e8552ead6911d28cd976ea365df78d3e8170921dd98a05f1d10", "bytes": 38573}
session cap 8.0 USD, warning 6.0 USD (env)
loop 1: t0 1793291640730
cost after: {"today_usd":0.124869,"total_usd":0.124869,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.124869,"session_cap_usd":8.0,"session_warn_usd":6.0}
state after: {'attachment_enabled': True}
app log: attachments INFO lines 0, covered=True 0
app stopped
```

The runner log (`rescore-paid-1029-2333-runner.log`) holds only the two lines quoted above (`session cap …`, `loop 1: t0 …`):
the runner was stopped before its loop summary.

## The stop, and what is missing because of it

The watcher (`rescore-paid-go-stage.sh`, polling every 10 s) applied rule (d) of the plan: at 23:36:00 (t = 122 s) the
ledger held 10 `comment_attachment` rows, all `expired`, and the app log had no `covered=True`, so it sent SIGTERM to the
`measure.c5` runner (`rescore-paid-1029-2333-watch.log`). The loop's 120 s had just elapsed, but the 30 s tail, the run
file `rescore-paid-1029-2333.json` (scorer, situations, `code_checks`, the worker and media process logs with their
`model_key_set` proofs, the `C5PROC` sha) and the last progress line were not written. Nothing in this note therefore
claims a situation result, a vision `done/skipped/stale` count from the worker, or a `model_key_set: true` proof; the
evidence that the key was set is the wrapper line `MODEL_API_KEY set: yes` and the 77 `ok: true` model calls with cost.

After the SIGTERM the worker process started by the runner (`measure.c5_proc worker --session rescore-paid-1029-2333`)
was left with parent pid 1 and kept its SSE stream open, so uvicorn stayed in `Waiting for connections to close` and the
wrapper's `wait` did not return; at 23:38 that worker process (this run's own, on port 8040) was sent SIGTERM by hand,
the app finished at 23:38:25 and the wrapper exited with the runner's rc. The watch log repeats its `STOP` line every
10 s during those two minutes; those repeats sent SIGTERM to a runner that was already gone.

## Order comments and the classification (c)

Ten order comments were labelled in the window (`comment_labeled` with `label = order`, distinct `comment_id`), and each
got an `expired` attachment 4000 ms after its ingestion (`deadline_ms − comment_ingested_ms` = 4000 on every row):

| comment | ts − t0 (ms) | reason | required frames R(C) | of them never read | later frame exists |
| --- | --- | --- | --- | --- | --- |
| c5-1-001 | 3460 | recovery-late: original attachment deadline expired | 4 | 3 | yes |
| c5-1-006 | 27450 | original attachment deadline expired | 28 | 20 | yes |
| c5-1-010 | 40750 | recovery-late: original attachment deadline expired | 41 | 28 | yes |
| c5-1-016 | 71100 | original attachment deadline expired | 72 | 50 | yes |
| c5-1-018 | 76850 | original attachment deadline expired | 77 | 52 | yes |
| c5-1-021 | 80100 | original attachment deadline expired | 81 | 54 | yes |
| c5-1-024 | 85850 | original attachment deadline expired | 86 | 56 | yes |
| c5-1-029 | 103600 | original attachment deadline expired | 94 | 60 | no |
| c5-1-031 | 107550 | original attachment deadline expired | 94 | 60 | no |
| c5-1-032 | 109000 | recovery-late: original attachment deadline expired | 94 | 60 | no |

"Never read" = a frame with `ts_ms ≤` the comment's `ts_ms` that is the `caused_by` of no `product_seen` model call.
The session has 94 frames (one per second, `t0` to `t0 + 93 s`) and 34 `product_seen` model calls, so 60 frames were
never sent to the vision model: with reads of 2.8 s on average (max 6.5 s) the vision lane keeps only the latest waiting
frame, and the skipped frames get no terminal record (`worker/attachments.py`, module line 1; rule (c) of the plan). Every
R(C) contains at least three such frames, so no readiness post could be made for any of the ten before its deadline,
and none was: the app log has no `attachment evaluation` line at all. The last three comments arrived after the last
frame, so they also lack the later frame that readiness needs. Classification, by the first matching rule of (c):
**missing read** for all ten; the `timing`, `unresolved` and `evidence rejected` classes need an evaluation line or a
complete terminal prefix, and there is neither. No evaluation line exists, so nothing is ambiguous.

This is a measurement of the worker as it is, not a proposal: with a real vision model at this frame rate, the rule that a
replaced frame is not a read leaves every order comment of the first 120 s without readiness evidence.

## Model calls (ledger, `keviq_paid_test`)

```
SELECT body->>'model', body->>'purpose', count(*), round(SUM((body->>'cost_usd')::numeric),4), round(avg((body->>'latency_ms')::numeric))
FROM events WHERE type='model_call' AND session_id='rescore-paid-1029-2333' GROUP BY 1,2;
muse-spark-1.3 | product_seen | 34 | 0.0453 | 2794
muse-spark-1.3 | label_comments | 28 | 0.0380 | 2425
muse-spark-1.3 | card_from_speech | 12 | 0.0322 | 4182
sam-3.1 | card_cutout | 2 | 0.0050 | 1048
muse-voice-transcribe-1.0 | transcribe | 1 | 0.0044 | 94054
SELECT count(*), SUM((body->>'cost_usd')::numeric) FROM events WHERE type='model_call' AND session_id='rescore-paid-1029-2333';
77 | 0.12486890
```

Events stored for the session by type: frame 94, model_call 77, comment_labeled 39, comment 34, speech 13, order 12,
comment_attachment 10, card 10, message_out 6, product_seen 2, price_proposal 1. The 77 rows are exported as
`rescore-paid-1029-2333-model-calls.jsonl` (one `row_to_json` per line, `seq` order) for the comparison with the
provider's billing page; `SUM(cost_usd)` of the file equals the SQL above.

## Files beside this note (sha256, first 16)

| File | sha256 |
| --- | --- |
| `rescore-paid-1029-2333-wrapper.log` | `fbd10b120f4c46a9` |
| `rescore-paid-1029-2333-runner.log` | `f88f44ccc4b473ea` |
| `rescore-paid-1029-2333-watch.log` | `b2bc2bfe58b3058a` |
| `rescore-paid-1029-2333-image-guard.log` | `34ef84cd7cb04087` |
| `rescore-paid-1029-2333-app.log` | `dcb7b953e099f782` |
| `rescore-paid-1029-2333-model-calls.jsonl` | `443c7f80132cf98a` |
| `rescore-paid-run.sh` | `31f7dee5d31ef812` |
| `rescore-paid-go-stage.sh` | `ba0d787284230631` |
| `rescore-paid-after-stage.sh` | `31af78fea2f68569` |
| `rescore-paid-log-config.json` | `24209442aaac4d61` |

Scratch paths inside the logs and scripts are reduced to `<scratch>`; the wrapper reads the key from the main checkout's
`.env` and prints only whether it is set. `rescore-paid-run.sh` is the wrapper as run (sha256 of the file in the table;
the plan's (b) section quotes the same text), `rescore-paid-go-stage.sh` the watcher, `rescore-paid-after-stage.sh` the
query script that produced the numbers above, `rescore-paid-log-config.json` the uvicorn log config.
