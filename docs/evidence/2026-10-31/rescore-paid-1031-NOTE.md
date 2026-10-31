# Paid bound rescore, stages 1 and 2: rescore-paid-1031-0737 and rescore-paid-1031-0742, on the label-lane branch

Two measured sessions on 31 October under the plan `../2026-10-29/rescore-paid-1030-PLAN.md` (sha256 first 16 `1ac69de2aec057c7`), run on the tree of
pull request #74 (head `4c53e720833c4cc895ae0f0901a9a2a790b446e7`, tree `f4b75feeb25b239bf150e831eb876296cfeefea2`, base main `062dade`), not on main: the branch changes only the label
lane of the worker (`worker/brain/labels.py`; a new comment no longer waits for an in-flight `label_comments` call of the same
session). Everything else is the 30 October procedure (`docs/evidence/2026-10-30/rescore-paid-1030-NOTE.md`): same catalog,
same bound set, same caps, same watcher rules, same ports and databases. Costs are real; nothing touched the project database.
Stage 1 is capped at 120 s and stage 2 at 350 s; the plan's condition for stage 2 was met in stage 1 with 9 `covered=True`
lines and 9 of 10 attachments `ready`, and stage 2 started 2 min 20 s after stage 1 ended. Neither stage was stopped:
runner rc 0 twice, the watcher never fired, port 8040 closed after each stage.

A first attempt at stage 1, `rescore-paid-1031-0144`, was started at 01:44 and lost when the host slept from 01:44:33 to
07:30:20 (system power log, not in the repository): the runner then closed the loop as `INCOMPLETE/INCOMPLETE behind
42`, the image guard reported `IMAGE_MISSING`, and 34 model calls (27 `ok`) for 0.03792415 USD stay in the ledger. Its
wrapper, watcher and runner logs are kept beside this note as the record of that cost; its other files are not, and
nothing of it is scored. The two stages below were run with the host kept awake (`caffeinate -i -s`).

Against 30 October: the label lane no longer makes a comment wait behind an in-flight call of its session (largest such
wait 1.06 s and 1.35 s against 2.5 s and 2.3 s), the `timing` class went from 2 and 5 comments to 1 and 1, and labels
later than 4 s from 4 and 6 to 2 and 3. Expirations in stage 2 stayed at 7 of 30 because a class that was empty on 30
October came back: five `missing read` comments, caused in the worker's vision lane by two reads cancelled at the 10 s
cap and by four-reader saturation under slower reads (detailed below). Provider latency was higher than on 30 October
for every purpose, which the branch does not control. Scorer: stage 1 PASS 4 / FAIL 4 (situation 12 went from PASS to
FAIL, 10 from NOT_TRIGGERED to PASS), stage 2 PASS 7 / FAIL 3 (10 from NOT_TRIGGERED to PASS; 3 accuracy 0.357 to 0.5 on
n=28; 4 passed 1 to 0 of 4; 6 triggered 3 to 2). Product findings about this tree, recorded, not fixed here.

## Comparison with 30 October

| | 30 Oct stage 1 (`rescore-paid-1030-2243`) | 31 Oct stage 1 (`rescore-paid-1031-0737`) | 30 Oct stage 2 (`rescore-paid-1030-2248`) | 31 Oct stage 2 (`rescore-paid-1031-0742`) |
|---|---|---|---|---|
| Tree | main `062dade` | PR #74 `4c53e72` | main `062dade` | PR #74 `4c53e72` |
| Model calls / cost (USD) | 174 / 0.26572490 | 172 / 0.25752100 | 491 / 0.75456405 | 499 / 0.80973035 |
| Start / stop | | 07:37:13 to 07:40:16 (app 07:37:14 to 07:40:13) | | 07:42:36 to 07:49:34 (app 07:42:37 to 07:49:26) |
| Runner | `loop 1: done, SETTLED/SETTLED behind 0, retired 3, waits {}, failures 0` | `loop 1: done, SETTLED/SETTLED behind 0, retired 3, waits {}, failures 0` | `… retired 10, waits {'many': 3}` | `… retired 12, waits {'many': 2}, failures 0` |
| Watcher (rule d) | `t=174 eval=9 cov=9 bad=0 … attachments=10 expired=3` | `t=173 eval=9 cov=9 bad=0 … attachments=10 expired=1` | `t=408 eval=26 cov=26 bad=0 … attachments=30 expired=7` | `t=408 eval=24 cov=24 bad=0 … attachments=30 expired=7` |
| Image guard | | `IMAGE_OK` 07:37:50, `card-154ad63c0d12` v6, 41285 bytes | | `IMAGE_OK` 07:43:08, `card-88c1f0946ec9` v5, 5023 bytes |
| Frames read | 120/120 | 120/120 | 350/350 | 348/350 (2 reads cancelled at the 10 s cap) |
| `comment_attachment` ready / expired | 7 / 3 | 9 / 1 | 23 / 7 | 23 / 7 |
| Classification (c): missing read / timing / unresolved | 0 / 2 / 1 | 0 / 1 / 0 | 0 / 5 / 2 | 5 / 1 / 1 |
| Wait from worker receipt to inference start, nothing in flight (median) | 1024 ms | 1029 ms | 1027 ms | 1030 ms |
| Comments received while a `label_comments` call of their session was in flight / largest wait to inference start | 17 of 34 / 2.47 s | 17 of 34 / 1.06 s | 41 of 100 / 2.30 s | 45 of 100 / 1.35 s |
| Batches of more than one comment / largest batch | 5 of 29 / 2 | 5 of 29 / 2 | 20 of 78 / 3 | 18 of 80 / 3 |
| Model label at the app after the comment (median / max) / after 4 s | 2.89 s / 5.43 s / 4 | 3.08 s / 4.72 s / 2 | 2.91 s / 5.51 s / 6 | 3.03 s / 5.77 s / 3 |
| Situations (scorer; 11 is TEST_ONLY, the rest NOT_TRIGGERED) | PASS 4 (2,5,9,12) FAIL 3 (1,3,4) | PASS 4 (2,5,9,10) FAIL 4 (1,3,4,12) | PASS 6 (1,2,6,7,9,12) FAIL 3 (3,4,5) | PASS 7 (1,2,6,7,9,10,12) FAIL 3 (3,4,5) |
| Ports / databases | 8040 / `keviq_paid_test` | same | same | same |

## Wrapper output (verbatim, `rescore-paid-1031-0737-wrapper.log`)

```
DATABASE_URL name: /keviq_paid_test
MODEL_API_KEY set: yes
binding: session rescore-paid-1031-0737 epoch epoch-rescore-paid-1031-0737 (two tokens generated, not shown)
migrations applied: none (up to date)
healthz: {"ok":true,"store":"pg","head_seq":2235}
cost before: {"today_usd":0.037924,"total_usd":1.183082,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.0,"session_cap_usd":8.0,"session_warn_usd":6.0}
cost pre-flight ok
runner rc=0
guard rc=0
{"t": "07:37:50", "kind": "IMAGE_OK", "cutout_call": "model_call_b1be91e5984440389d9d", "card_id": "card-154ad63c0d12", "version": 6, "event_id": "card_c33c7c38057a40138b6e", "image_sha256": "614d33a9f2a972dc3f853ffae379b4aa499b2a0104a67ecef8d8da15ced6c639", "bytes": 41285}
session cap 8.0 USD, warning 6.0 USD (env)
loop 1: t0 1793407035702
loop 1: done, SETTLED/SETTLED behind 0, retired 3, waits {}, failures 0
run rescore-paid-1031-0737 sha 4c53e720833c4cc895ae0f0901a9a2a790b446e7 loops 1
SELECT session_id, count(*) AS n, COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd
FROM events WHERE type = 'model_call' AND session_id = 'rescore-paid-1031-0737' GROUP BY session_id;
session_id | n | usd
rescore-paid-1031-0737 | 172 | 0.25752100
(1 rows)
cost after: {"today_usd":0.295445,"total_usd":1.440603,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.257521,"session_cap_usd":8.0,"session_warn_usd":6.0}
state after: {'attachment_enabled': True}
app log: attachments INFO lines 9, covered=True 9
app stopped
```

## Wrapper output (verbatim, `rescore-paid-1031-0742-wrapper.log`)

```
DATABASE_URL name: /keviq_paid_test
MODEL_API_KEY set: yes
binding: session rescore-paid-1031-0742 epoch epoch-rescore-paid-1031-0742 (two tokens generated, not shown)
migrations applied: none (up to date)
healthz: {"ok":true,"store":"pg","head_seq":2711}
cost before: {"today_usd":0.295445,"total_usd":1.440603,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.0,"session_cap_usd":8.0,"session_warn_usd":6.0}
cost pre-flight ok
runner rc=0
guard rc=0
{"t": "07:43:08", "kind": "IMAGE_OK", "cutout_call": "model_call_f99ace65b0b64e128baa", "card_id": "card-88c1f0946ec9", "version": 5, "event_id": "card_9b0ebf3a72f541a0971a", "image_sha256": "34bfd130e960e93309d4882789765eaac3f5d448e835a58e32a13803007c1058", "bytes": 5023}
session cap 8.0 USD, warning 6.0 USD (env)
loop 1: t0 1793407359219
loop 1: done, SETTLED/SETTLED behind 0, retired 12, waits {'many': 2}, failures 0
run rescore-paid-1031-0742 sha 4c53e720833c4cc895ae0f0901a9a2a790b446e7 loops 1
SELECT session_id, count(*) AS n, COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd
FROM events WHERE type = 'model_call' AND session_id = 'rescore-paid-1031-0742' GROUP BY session_id;
session_id | n | usd
rescore-paid-1031-0742 | 499 | 0.80973035
(1 rows)
cost after: {"today_usd":1.105176,"total_usd":2.250333,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.80973,"session_cap_usd":8.0,"session_warn_usd":6.0}
state after: {'attachment_enabled': True}
app log: attachments INFO lines 24, covered=True 24
app stopped
```

## Order comments and the classification (c)

Method and rule (c) as in the 30 October note (`R(C)`, read = a `product_seen` call naming the frame in `caused_by`,
evaluation lines attributed by `deadline_ms = wall-clock ms + remaining_ms`, exact to 0 ms for all 9 and 24 lines, no
line left over; classes in order missing read, timing, unresolved, evidence rejected, ready; `deadline_ms =
comment_ingested_ms + 4000` on every row). A read that was cancelled at the lane's cap counts as still running at the
deadline. App logs of both stages: 0 lines with status 401, 422 or 5xx; 432 and 1272 `POST` lines; the readiness posts
of the worker are not logged as separate lines on this tree, so the watcher's `eval`/`cov` counts stand for them.

### Stage 1: rescore-paid-1031-0737 (t0 1793407035702)

| comment | ingested − t0 (ms) | deadline − t0 | outcome (reason) | `R(C)` | of them read | candidates | card | class |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| c5-1-001 | 3471 | 7471 | expired (original) | 4 | 4 | 0 | - | timing |
| c5-1-006 | 27462 | 31462 | ready | 28 | 28 | 1 | card-154ad63c0d12 | ready |
| c5-1-010 | 40762 | 44762 | ready | 41 | 41 | 1 | card-154ad63c0d12 | ready |
| c5-1-016 | 71108 | 75108 | ready | 72 | 72 | 2 | card-53056dd64cfe | ready |
| c5-1-018 | 76864 | 80864 | ready | 77 | 77 | 2 | card-154ad63c0d12 | ready |
| c5-1-021 | 80112 | 84112 | ready | 81 | 81 | 2 | card-53056dd64cfe | ready |
| c5-1-024 | 97913 | 101913 | ready | 86 | 86 | 2 | card-53056dd64cfe | ready |
| c5-1-029 | 103616 | 107616 | ready | 104 | 104 | 2 | card-19bc3e884d8a | ready |
| c5-1-031 | 107569 | 111569 | ready | 108 | 108 | 2 | card-b8db5a9d8b1d | ready |
| c5-1-032 | 109014 | 113014 | ready | 110 | 110 | 2 | card-19bc3e884d8a | ready |

### Stage 2: rescore-paid-1031-0742 (t0 1793407359219)

| comment | ingested − t0 (ms) | deadline − t0 | outcome (reason) | `R(C)` | of them read | candidates | card | class |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| c5-1-001 | 3482 | 7482 | expired (original) | 4 | 4 | 0 | - | unresolved |
| c5-1-006 | 27462 | 31462 | ready | 28 | 28 | 1 | card-88c1f0946ec9 | ready |
| c5-1-010 | 40764 | 44764 | ready | 41 | 41 | 1 | card-88c1f0946ec9 | ready |
| c5-1-016 | 71106 | 75106 | ready | 72 | 72 | 2 | card-466573bb259c | ready |
| c5-1-018 | 76873 | 80873 | ready | 77 | 77 | 2 | card-88c1f0946ec9 | ready |
| c5-1-021 | 80108 | 84108 | ready | 81 | 81 | 2 | card-466573bb259c | ready |
| c5-1-024 | 97917 | 101917 | ready | 86 | 86 | 2 | card-466573bb259c | ready |
| c5-1-029 | 103621 | 107621 | ready | 104 | 104 | 2 | card-85fa45928658 | ready |
| c5-1-031 | 107565 | 111565 | expired (original) | 108 | 107 | 2 | - | missing read |
| c5-1-032 | 109014 | 113014 | expired (original) | 110 | 109 | 2 | - | missing read |
| c5-1-035 | 129904 | 133904 | ready | 113 | 113 | 2 | card-85fa45928658 | ready |
| c5-1-036 | 120853 | 124853 | expired (recovery-late) | 121 | 121 | 2 | - | timing |
| c5-1-041 | 136151 | 140151 | expired (original) | 137 | 133 | 2 | - | missing read |
| c5-1-043 | 142587 | 146587 | ready | 143 | 143 | 2 | card-a697c167358f | ready |
| c5-1-045 | 147058 | 151058 | ready | 148 | 148 | 2 | card-85fa45928658 | ready |
| c5-1-047 | 149030 | 153030 | ready | 150 | 150 | 2 | card-a697c167358f | ready |
| c5-1-048 | 155487 | 159487 | ready | 156 | 156 | 2 | card-a697c167358f | ready |
| c5-1-054 | 178693 | 182693 | ready | 179 | 179 | 2 | card-1eb8349645c4 | ready |
| c5-1-055 | 180305 | 184305 | ready | 181 | 181 | 2 | card-a697c167358f | ready |
| c5-1-056 | 181429 | 185429 | ready | 182 | 182 | 2 | card-1eb8349645c4 | ready |
| c5-1-060 | 200863 | 204863 | ready | 201 | 201 | 2 | card-876b77ad6308 | ready |
| c5-1-062 | 207564 | 211564 | ready | 208 | 208 | 2 | card-876b77ad6308 | ready |
| c5-1-071 | 235506 | 239506 | ready | 236 | 236 | 2 | card-3e1618efd6f6 | ready |
| c5-1-074 | 240314 | 244314 | ready | 241 | 241 | 2 | card-3e1618efd6f6 | ready |
| c5-1-078 | 258618 | 262618 | ready | 259 | 259 | 2 | card-8b501aeb97f8 | ready |
| c5-1-082 | 277083 | 281083 | expired (original) | 278 | 277 | 2 | - | missing read |
| c5-1-085 | 286230 | 290230 | ready | 287 | 287 | 2 | card-8cf284a6cfa8 | ready |
| c5-1-091 | 320057 | 324057 | expired (original) | 321 | 320 | 2 | - | missing read |
| c5-1-095 | 335837 | 339837 | ready | 336 | 336 | 2 | card-5932345f8adc | ready |
| c5-1-098 | 339890 | 343890 | ready | 340 | 340 | 2 | card-5932345f8adc | ready |

### The unresolved and timing rows

Method as on 30 October: gap = `emitted_ms` of the last required read minus `deadline_ms`, read length = that call's
`latency_ms`, label arrival = `ingested_ms` of the `comment_labeled` row that the expiry row's `label_event_id` names,
snapshot = the expiry row's `snapshot_at_ms`.

| stage | comment | class | last required read − deadline (ms) | read length (ms) | next frame's read − deadline (ms) | label arrival − deadline (ms) | expiry snapshot − deadline (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | c5-1-001 | timing (0 candidates) | −1529 | 2678 | −941 | +61 | +68 |
| 2 | c5-1-001 | unresolved | −1936 | 2280 | −874 | −91 | +9 |
| 2 | c5-1-031 | missing read | +5649 | 9987 (cancelled) | −1120 | −997 | +6 |
| 2 | c5-1-032 | missing read | +4200 | 9987 (cancelled) | −1021 | −684 | +8 |
| 2 | c5-1-036 | timing | −1059 | 3465 | +631 | +1023 | +1032 |
| 2 | c5-1-041 | missing read | +1863 | 2780 | +3017 | −1194 | +5 |
| 2 | c5-1-082 | missing read | +2986 | 4620 | +3234 | −1212 | +5 |
| 2 | c5-1-091 | missing read | +1368 | 4162 | −392 | −1162 | +4 |

The first order comment of each loop (`c5-1-001`, t0 + 3.5 s) again arrives before any card exists: the first `card` of
the loop is at t0 + 20.7 s (stage 1) and t0 + 19.3 s (stage 2), the first `speech` at t0 + 12.5 s and 13.1 s (30
October: 2.9 s and 3.0 s), the first `product_seen` at t0 + 20.6 s and 19.3 s. In stage 2 the comment was evaluated once
inside the window (07:42:46.622, `resolved=False`, 79 ms left) with 0 candidates: `unresolved`. In stage 1 its label
reached the app 61 ms after the deadline, so no evaluation ran before the sweep; rule (c) files it under `timing`
because the last required read had finished 1.5 s before the deadline, but with 0 candidates it would have expired as
`unresolved` in any case. The label itself took 4061 ms from the comment's ingestion to its arrival at the app
(worker receipt after 8 ms, 1045 ms to inference start, inference 3003 ms, 5 ms to
the app); in stage 2 the same comment's label took 3909 ms (14 / 1058 / 2831 / 6 ms).

Timing: `c5-1-036` (stage 2) is the only comment of the two stages whose label reached the app after its deadline
(1023 ms after it; the expiry row's reason is `recovery-late: original attachment deadline expired`, the sweep ran when the label arrived). The frame reads it
needed had finished 1.1 s before the deadline. The label took 5023 ms: worker receipt 10 ms after
ingestion, 1040 ms to inference start (nothing of the label lane in the way beyond the batch window),
inference 3960 ms, 13 ms to the app. On 30 October seven comments were in this class, each having
waited 0.4 to 2.5 s behind an in-flight `label_comments` call of the same session; today none waited behind one for more
than the batch window (largest wait to inference start among the 17 and 45 comments that arrived while a call of their
session was in flight: 1059 and 1348 ms, against 2473 and 2296 ms on 30 October), which is the change the branch makes.

Missing read: five comments of stage 2 expired because a frame of `R(C)` was still being read at the deadline: the class
that was empty on 30 October and full (10 of 10) on 29 October. Two causes, both in the vision lane of the worker,
which the branch does not touch:

- Two `product_seen` reads hit the lane's 10 s cap (`VISION_TIMEOUT_S` in `worker/runner.py`) and were cancelled:
  frame t0 + 107.0 s (`model_call_52836fc0863745f992c3`, latency 9987 ms) and frame t0 + 129.0 s
  (`model_call_c9cec5f8ffa94aee8a06`, 9984 ms). Both ledger rows have `ok=false`, `error: "cancelled"`, cost 0 and 0
  tokens; neither frame was read again (worker progress `errors 2`, vision `done 348` of 350). `c5-1-031` and `c5-1-032`
  both needed frame t0 + 107.0 s.
- The lane reads at most four frames of a bound session at once (`VISION_READS_PER_SESSION`, default 4). With one frame
  per second it saturates as soon as reads average more than 4 s, and a cancelled call holds a reader for 10 s. Read
  starts (`emitted_ms − latency_ms − ingested_ms`) were 16 to 298 ms on 30 October for every frame; today the median is
  22 ms but 64 of 350 reads started more than 300 ms after the frame, up to 6063 ms: t0 + 111 to 125 s and 133 to 143 s
  (behind the two cancelled calls), and again around t0 + 223 to 236 s, 273 to 287 s and 315 to 328 s, where reads of 3 to
  4.9 s alone filled the four readers. `c5-1-041`, `c5-1-082` and `c5-1-091` fall in those stretches: their labels were
  in time (1.2 s before the deadline) and the read that was missing finished 1.4 to 3.0 s after it. Stage 1 had no
  cancelled call; its two latest read starts were 1970 and 901 ms (frames t0 + 17 and 18 s).

Every `product_seen` call was slower this morning than on 30 October (average 2368 and 2559 ms against 2144 and 2134
ms, see the ledger table), as was every other purpose; that is the provider's latency on the day, not the tree. It
also explains why the median label time at the app rose slightly (3.08 and 3.03 s against 2.89 and 2.91 s) although the
wait behind in-flight batches is gone: `label_comments` inference averaged 2123 and 2079 ms against 1926 and 1928 ms.
Labels later than 4 s after the comment fell from 4 to 2 (stage 1) and from 6 to 3 (stage 2). One run per stage on one
morning cannot separate the two effects further; the wait-behind-in-flight measure is the direct evidence for the change.

## The 0 USD e2e on the same tree

The 0 USD e2e of 30 October (`docs/evidence/2026-10-30/e2e-main-1030-1852-NOTE.md`) was repeated on the checkout of
the paid stages, detached at `4c53e720833c4cc895ae0f0901a9a2a790b446e7` (tree `f4b75feeb25b239bf150e831eb876296cfeefea2`),
after both stages had ended: run ids `e2e-A-1031-0755` (dry) and `e2e-A-1031-0755s` (stand-in app on port 8040), the
test database `keviq_test` through `tests.db_lock`, the model key emptied by the lock wrapper (`MODEL_API_KEY set: no`).
The driver started at 07:55:07 and ended at 08:17:21 (ICT); `make check` ran on the same tree afterwards, not
concurrently. Every step returned rc 0 and neither session wrote a `model_call` row.

| Step | Command | Output |
| --- | --- | --- |
| 1 | `make check` (after the e2e, same tree) | rc 0; `All checks passed!`; `hygiene: checked 3 commit(s) in 062dade..4c53e72` / `hygiene: clean`; `1489 passed in 480.11s (0:08:00)` (no skips: `data/local/test1` present; 1482 + 7 on the author's run) (`e2e-A-1031-0755-make-check.log`) |
| 2 | `uv run python -m tests.db_lock -- python -m measure.c5 --dry --loops 1 --no-code-check --run e2e-A-1031-0755 --out <scratch>/c5` | rc 0; `loop 1: done, retired 11, waits {'many': 1}, failures 0`; ledger `(0 rows)` (`e2e-A-1031-0755-dry-runner.log`) |
| 3 | trial app `uvicorn app.main:app --host 127.0.0.1 --port 8040` under the lock, then `python -m measure.c5 --base-url http://127.0.0.1:8040 --loops 1 --seconds 330 --run e2e-A-1031-0755s --out <scratch>/c5`, four steps, SSE check, ledger count | runner rc 0; `loop 1: done, SETTLED/SETTLED behind 0, retired 10, waits {'many': 1}, failures 0`; `four steps rc=0`; `mid: done rc=0`; `model_call rows, session e2e-A-1031-0755s: 0` (`e2e-A-1031-0755s-runner.log`, `e2e-A-1031-0755s-sse-check.log`) |
| 4a | `uv run python -m tests.db_lock -- env SLOW_VISION_REPORT=<scratch>/testc-compressed.jsonl uv run python -m pytest -q -s tests/test_slow_vision_integration.py --tb=short` | `5 passed in 92.62s (0:01:32)` (`e2e-A-1031-0755-testc-compressed.log`) |
| 4b | `uv run python -m tests.db_lock -- env SLOW_VISION_SPEED=1 SLOW_VISION_REPORT=<scratch>/testc-full-speed.jsonl uv run python -m pytest -q -s tests/test_slow_vision_integration.py --tb=short` | `5 passed in 430.19s (0:07:10)` (`e2e-A-1031-0755-testc-full-speed.log`) |
| after | `SELECT session_id, count(*) AS n, COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd FROM events WHERE type = 'model_call' AND session_id = '<run>' GROUP BY session_id;` for both ids | `(0 rows)` and `(0 rows)`; `events, session e2e-A-1031-0755: 466 seq 569680..570145`; `events, session e2e-A-1031-0755s: 764 seq 570146..570909` |

Against 30 October nothing changed in the outcomes. The dry run posted 100 comments and wrote 466 events
(`comment_labeled 110, comment 100, order 77, speech 64, message_out 54, card 42, product_seen 12, message_in 4,
price_proposal 3`), the stand-in run 92 comments and 764 events (`frame 330, comment_labeled 102, comment 92,
order 71, speech 62, message_out 50, card 39, product_seen 11, message_in 4, price_proposal 3`): the same counts and
distributions as on 30 October. The twelve situations of the scorer have the same status in both runs as on
30 October: PASS 1, 2, 5, 6, 7, 8, 9, 10 and 12; FAIL 3 (`accuracy 0.5714285714285714 on n=28` in the dry run,
16 of 26 in the stand-in run, target 0.9) and 4 (`ask_variant <= 5 s`, 0 of 4); TEST_ONLY 11. Interventions 22 (dry)
and 20 (stand-in); needs-seller entries 19 and 17 (variant 4, over_stock 13 and 11, price_changed 1, discount 1);
receipts 2 of 2 at the confirmed version; order recall 1.0; session minutes 6.32 and 6.01; phone visible at most
0.043 s and 0.031 s (0.064 s and 0.043 s on 30 October, n 4). The e2e exercises the changed `_consume` path with the
gold label client, whose calls return at once, so it shows that the change broke nothing in the flows; the timing
effect of the change is measured only by the paid stages above.

Four steps, by sequence number. Dry run (`e2e-A-1031-0755-four-steps.txt`, header `events 466 orders 27 confirmed 2`):
`o-c5-1-016` (card-1-00, SP01) price_proposal seq 569728 (heard 220000, current 250000, card_version 4), card v5
220000 seq 569734 edited by the seller because of the proposal, DRAFT 569747, NEEDS_SELLER 569748 (variant size 40
not on v5), CONFIRM_SENT 569754 (seller approved, unit price = card price), message_in `confirm_yes` 569775,
CONFIRMED 569776, receipt message_out 569777 at card version 5, `version_exists True price_matches_version True
image False`; `o-c5-1-029` (card-1-01, SP02) DRAFT 569810, NEEDS_SELLER 569811 (size 41 not on v3), CONFIRM_SENT
569818, message_in 569834, CONFIRMED 569835, receipt 569836 at v3 320000. Stand-in run
(`e2e-A-1031-0755s-four-steps.txt`, header `events 764 orders 25 confirmed 2`): the same path with seq 570249
(proposal), 570259 (card v5), 570285/570286/570297, 570322 (message_in), 570323 (CONFIRMED), 570325 (receipt v5)
for `o-c5-1-016`, and 570380/570381/570392, 570414, 570415, 570416 (receipt v3) for `o-c5-1-029`.

SSE close and reopen, verbatim from `e2e-A-1031-0755s-sse-check.log`:

```text
sse1: 764 ids, killed after 1 s, last id 570909
healthz at once: 200
healthz +5 s: 200
sse2 reopened with Last-Event-ID 570909: first id none, last id none (0 ids in 3 s)
healthz after: {"ok":true,"store":"pg","head_seq":570913}
  mid: healthz: {"ok":true,"store":"pg","head_seq":570913}
  mid: sse3 reopened with Last-Event-ID 570603: first id 570604, last id 570909 (306 ids in 3 s)
  mid: same ids as the full stream after 570603 (no gap, no repeat): yes
  mid: contiguous (+1 each): yes
  mid: done rc=0
```

TEST-C (`tests/test_slow_vision_integration.py`), SLOW_VISION summary from the two logs, 30 October in brackets:

| | compressed (speed 20) | full speed (speed 1) |
| --- | --- | --- |
| frames / comments | 351 / 100 | 351 / 100 |
| digit phone hidden | 3/3 | 3/3 |
| hide latency, logical ms (c5-1-002 / 007 / 041) | 180 / 180 / 280 (200 / 360 / 280) | 14 / 12 / 10 (20 / 22 / 21) |
| model_calls | 0 | 0 |
| vision done / skipped (failed) | 153 / 198 (0) (146 / 205) | 175 / 176 (0) (174 / 177) |
| barrier | SETTLED, errors 0 | SETTLED, errors 0 |
| wall | 19.2 s (19.2 s) | 354.6 s (354.2 s) |
| pytest | `5 passed in 92.62s` (77.37 s) | `5 passed in 430.19s` (415.24 s) |

`drain` and `stopped` cases: SETTLED after the drain, INCOMPLETE kept while stopped, as the test expects. The
done/skipped split moves with the machine's timing, as it did between 29 and 30 October; no cause is claimed. Both
pytest wall times are about 15 s longer than on 30 October; no cause was measured.

As on 30 October, the `cost before` / `cost after` lines that the stand-in script prints from the trial app's
`/api/cost` are not in the copied SSE check log: on the shared test database that endpoint aggregates every test
session, not this run. The stand-in app log, the worker's and media's `loop1` logs, the three raw SSE captures, the
step log of the dry run and the two `testc-*.jsonl` reports are not kept, as on 30 October.

## Model calls (ledger, keviq_paid_test)

The session totals are in the wrapper output above (the wrapper's `SUM` query). By purpose and model, with `count`,
`SUM(cost_usd)`, average and maximum `latency_ms`. Every row of stage 1 has `ok = true`; in stage 2 the two cancelled
`product_seen` reads named above have `ok = false` (348 of 350 true), `cost_usd` 0 and `latency_ms` 9987 and 9984, which
is the stage 2 maximum of that row:

| purpose | model | stage 1 calls | USD | avg ms | max ms | stage 2 calls | USD | avg ms | max ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `product_seen` | muse-spark-1.3 | 120 | 0.15991330 | 2368 | 5857 | 350 | 0.51092430 | 2559 | 9987 |
| `label_comments` | muse-spark-1.3 | 29 | 0.03735370 | 2123 | 3654 | 80 | 0.10914805 | 2079 | 4701 |
| `card_from_speech` | muse-spark-1.3 | 19 | 0.04675400 | 3796 | 4857 | 57 | 0.14465800 | 3817 | 6149 |
| `card_cutout` | sam-3.1 | 3 | 0.00750000 | 1276 | 2115 | 11 | 0.02750000 | 1020 | 1393 |
| `transcribe` | muse-voice-transcribe-1.0 | 1 | 0.00600000 | 120841 | 120841 | 1 | 0.01750000 | 350812 | 350812 |
| total | | 172 | 0.25752100 | | | 499 | 0.80973035 | | |

Events of each session by type:

| type | stage 1 | stage 2 |
| --- | --- | --- |
| `model_call` | 172 | 499 |
| `frame` | 120 | 350 |
| `comment_labeled` | 45 | 124 |
| `comment` | 34 | 100 |
| `card` | 26 | 103 |
| `speech` | 21 | 59 |
| `order` | 18 | 38 |
| `product_seen` | 10 | 45 |
| `comment_attachment` | 10 | 30 |
| `message_out` | 12 | 24 |
| `message_in` | 2 | 2 |
| `price_proposal` | 1 | 2 |

The `model_call` rows of each session are exported to `rescore-paid-1031-<run>-model-calls.jsonl` (one row per line; the
`transcribe` row is the single call that spans the loop, 120.8 s and 350.8 s). All sessions of the ledger database after
stage 2 (`count`, `ok = true`, `SUM(cost_usd)`):

```
rescore-paid-1029-2333 | 77 | 76 | 0.12486890
rescore-paid-1030-2243 | 174 | 174 | 0.26572490
rescore-paid-1030-2248 | 491 | 491 | 0.75456405
rescore-paid-1031-0144 | 34 | 27 | 0.03792415
rescore-paid-1031-0737 | 172 | 172 | 0.25752100
rescore-paid-1031-0742 | 499 | 497 | 0.80973035
all sessions | 1447 | 1437 | 2.25033335
```

The three sessions of 31 October cost 1.10517550 USD together (the app's `/api/cost` after stage 2: `today_usd`
1.105176, `total_usd` 2.250333). The provider's billing page was not read for this note; the costs above are the
ledger's.

## Files beside this note (sha256, first 16)

| file | sha256 | bytes |
| --- | --- | --- |
| `e2e-A-1031-0755-comments.jsonl` | `eeeed03698f8b81a` | 36183 |
| `e2e-A-1031-0755-dry-runner.log` | `29eb931eb40767ce` | 524 |
| `e2e-A-1031-0755-four-steps.txt` | `82c63ca225f0a342` | 2031 |
| `e2e-A-1031-0755-make-check.log` | `230927bdce6d3740` | 2677 |
| `e2e-A-1031-0755-scenarios.json` | `87c640e5e1b5a64f` | 3583 |
| `e2e-A-1031-0755-testc-compressed.log` | `39eb2e5e9043c0a2` | 2721 |
| `e2e-A-1031-0755-testc-full-speed.log` | `22ec56a6d13be550` | 2719 |
| `e2e-A-1031-0755.json` | `3a3aa62e8891e2b7` | 41842 |
| `e2e-A-1031-0755s-comments.jsonl` | `83b2bf737119efda` | 33296 |
| `e2e-A-1031-0755s-four-steps.txt` | `e9430df8c8e9226c` | 2034 |
| `e2e-A-1031-0755s-runner.log` | `03dbb95c3da3b2ac` | 598 |
| `e2e-A-1031-0755s-scenarios.json` | `7cbd42c5f6b380a1` | 3556 |
| `e2e-A-1031-0755s-sse-check.log` | `45cc13b1766d349c` | 3279 |
| `e2e-A-1031-0755s-sse-mid.log` | `0edde3540f2eb4ee` | 244 |
| `e2e-A-1031-0755s.json` | `4d368e82d341ed7a` | 50552 |
| `rescore-paid-1031-0144-runner.log` | `205f9ee60f73256a` | 684 |
| `rescore-paid-1031-0144-watch.log` | `5605999b595a4444` | 3242 |
| `rescore-paid-1031-0144-wrapper.log` | `a9be5695ad46c384` | 1523 |
| `rescore-paid-1031-0737-app.log` | `ab4dfea986de6c7a` | 144026 |
| `rescore-paid-1031-0737-comments.jsonl` | `a6ae6cf0fac50686` | 12163 |
| `rescore-paid-1031-0737-image-guard.log` | `9132d7c9862aae90` | 725 |
| `rescore-paid-1031-0737-loop1-media.log` | `b803baab2c62b5a4` | 20543 |
| `rescore-paid-1031-0737-loop1-worker.log` | `209e17945258f6f7` | 280166 |
| `rescore-paid-1031-0737-model-calls.jsonl` | `0eee897fdfd7ce38` | 105523 |
| `rescore-paid-1031-0737-runner.log` | `f316d68dcf1d4aec` | 678 |
| `rescore-paid-1031-0737-scenarios.json` | `1c41b2038e095568` | 3486 |
| `rescore-paid-1031-0737-watch.log` | `340c4d16851cd651` | 1710 |
| `rescore-paid-1031-0737-wrapper.log` | `98329547ab87e975` | 1694 |
| `rescore-paid-1031-0737.json` | `d27515bed784c587` | 26680 |
| `rescore-paid-1031-0742-app.log` | `4ce07750346b57d1` | 394134 |
| `rescore-paid-1031-0742-comments.jsonl` | `bf64446206806294` | 35100 |
| `rescore-paid-1031-0742-image-guard.log` | `005526b23c3c35bf` | 723 |
| `rescore-paid-1031-0742-loop1-media.log` | `dd1cd4cfc82fe642` | 57259 |
| `rescore-paid-1031-0742-loop1-worker.log` | `663f91c82ff05b94` | 775057 |
| `rescore-paid-1031-0742-model-calls.jsonl` | `229c97bb3f4e0846` | 306334 |
| `rescore-paid-1031-0742-runner.log` | `c4e4a81b25299841` | 688 |
| `rescore-paid-1031-0742-scenarios.json` | `39ff77ca4ca64ebc` | 3553 |
| `rescore-paid-1031-0742-watch.log` | `b4c06ad304b025d6` | 3924 |
| `rescore-paid-1031-0742-wrapper.log` | `048bccc154d0b380` | 1704 |
| `rescore-paid-1031-0742.json` | `73032144c50499d4` | 41505 |

The wrapper, the watcher, the query script and the uvicorn log config are the tracked 29 October files
`../2026-10-29/rescore-paid-run.sh` (`b19004443a8901f0`), `rescore-paid-go-stage.sh` (`ba0d787284230631`),
`rescore-paid-after-stage.sh` (`31af78fea2f68569`) and `rescore-paid-log-config.json` (`24209442aaac4d61`); the copies
used today were byte-identical and are not duplicated here. The wrapper reads the key from the checkout's `.env` and
prints only whether it is set; the files beside this note carry no home or scratch paths. The worker's and media's
`loop1` logs of the discarded run are not kept.
