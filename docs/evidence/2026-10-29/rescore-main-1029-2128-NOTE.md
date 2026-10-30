# Rescore on merged main — rescore-main-1029-2128

Unpaid replay of the stored observation set of `diag-test1x1-1028-1531` against the app, worker and order
code of main `f2c2dba` (the merge of PR #61, media-time attachment binding). Purpose: the rescore that the
baseline `rescore-base-1029-194618` (main `a767f02`) was taken for, so that the two can be compared number
for number.

| Item | Value |
| --- | --- |
| Model calls | 0 — `MODEL_API_KEY` blank, `model_key_set: false` in the run file (twice, worker and media process), `model_call` rows for the session: 0 (SQL `(0 rows)`, printed by the runner) |
| Run id | `rescore-main-1029-2128` |
| Revision | `7dc77430edf56ba3cec56cd77c9ce321c7dd8a35` = `f2c2dba` + measure-only replay support (below) |
| `make check` on this revision | rc 0 — `All checks passed!`, `1359 passed, 2 xfailed in 70.19s (0:01:10)` |
| Observation set | `observations.jsonl` sha256 `f48b69a95b969504dd809a769cd622849921237d17c52a1694bf758f67465f9a`, 1,096 events (11 product_seen, 64 speech, 351 frames), kept outside git; the same file as the baseline |

## What is replayed and what is not

- Same replay as the baseline: the diag run's `product_seen` and `speech` events are re-emitted with their
  original offsets from the diag's first frame (`measure/c5_frozen.py`); frames come from the stored test1
  frame sequence through the media process; comments and the scripted shop are the offline C5 scenario, one loop.
  No vision call is made; the model-label error of 28 October is kept as it was.
- `git diff --stat f2c2dba..7dc7743` lists only `measure/c5.py`, `measure/c5_frozen.py`, `measure/c5_live.py`,
  `tests/test_c5_frozen.py` and the baseline evidence files; `app/`, `worker/` and `orders/` are those of `f2c2dba`.
- The replay session has no attachment binding. PR #61 enters the media-time flow only through a server-side
  binding (`docs/attachment-runtime.md`: unregistered sessions keep the existing flow), and the replay runner
  creates none: `grep -rl attachment measure/` matches no file, and the worker log of this run has no
  attachment line (`grep -c attachment <loop1-worker.log>` = 0). The session therefore ran the legacy flow, which is why every column below equals the
  baseline. `docs/attachment-runtime.md` itself lists the frozen-workload rescore as separate verification
  work; a replay that exercises the binding needs a bootstrap in the runner, which this run does not add.
- The scripted shop still has no item-choice action, so seller SKU choices are 0 by construction. The
  projected intervention rate adds one choice per card that never received a SKU.

## Execution and isolation

Trial app on `127.0.0.1:8040` (ports 8000 and 8001 were not touched; 8040 answered nothing before the start),
test database `keviq_test` through the shared lock (`python -m tests.db_lock`; the first attempt returned rc 2
because another run held the lock, the wrapper retried after 10 s and ran under its own lock, the foreign lock
was not removed), `MODEL_SESSION_CAP_USD=8`, `MODEL_SESSION_WARN_USD=6`, `CATALOG_PATH=data/comments/c5/catalog.json`.

```
uv run python -m tests.db_lock -- bash rescore-run.sh rescore-main-1029-2128 <observations.jsonl> <out> 8040
DATABASE_URL name: /keviq_test
MODEL_API_KEY set: no
migrations applied: none (up to date)
healthz: {"ok":true,"store":"pg","head_seq":206125}
runner rc=0
```

Runner (`measure.c5 --base-url http://127.0.0.1:8040 --loops 1 --run rescore-main-1029-2128 --observations <observations.jsonl>`):

```
session cap 8.0 USD, warning 6.0 USD (env)
loop 1: t0 1793284108014
loop 1: done, SETTLED/SETTLED behind 0, retired 10, waits {'many': 2}, failures 0
run rescore-main-1029-2128 sha 7dc77430edf56ba3cec56cd77c9ce321c7dd8a35 loops 1
SELECT session_id, count(*) AS n, COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd
FROM events WHERE type = 'model_call' AND session_id = 'rescore-main-1029-2128' GROUP BY session_id;
session_id | n | usd
(0 rows)
```

Events stored for the session: 802 (frame 351, comment 100, comment_labeled 110, message_out 52, speech 64,
product_seen 11, card 39, order 71, message_in 3, price_proposal 1) — the same counts as the baseline. The lock
was released afterwards.

## Results (one loop, 100 comments, 28 references, 30 order comments)

| Measure | Main `f2c2dba` (this run) | Baseline `a767f02` | Diag 28 Oct (paid, revision 650fe39) |
| --- | --- | --- | --- |
| References right | 8/28 = 0.286 (current_card 6/24, previous_card 2/4) | 8/28 = 0.286 (6/24, 2/4) | 7/28 = 0.25 |
| Reference failures | no_card 2; item card live after the comment 11; live before 4; never live 3 | same | — |
| Receipts, pinned version correct | 2/2 | 2/2 | 2/2 |
| Receipts, right item against gold | 0/2 (o-c5-1-016 card-1-00 SP01 v4 vs gold SP02; o-c5-1-029 card-1-01 SP02 v3 vs gold SP03) | 0/2, same orders | 0/2 |
| Order comments by outcome | order 25, unresolved 4, ask_variant 1 | same | — |
| Abstentions | 4 unresolved drafts → 4 `ask_which_item` messages; 1 `ask_variant` message | same | — |
| Cards opened | 11 for 10 items; 6 without SKU (SP03, SP07, SP08, SP09, SP10, SP01 return); 0 still draft at loop end | same | 9; SP07–SP10 without SKU; SP03 reused the SP02 card |
| Duplicate cards per item | SP01: 2 (`card-1-00` at +22 s, `card-1-10` at +333 s, the return) | same | 0 |
| Seller SKU choices | 0 (no such shop action) | 0 | 0 |
| Interventions, scripted | 19 over 6.38 min = 178.69/hour | 19, 178.73/hour | 19, 178.62/hour |
| Interventions by cause | variant 8, over_stock 7, price 1, discount 1, card_state 1, other 1 | same | variant 10, over_stock 7, price 1, discount 1 |
| Projected with one SKU choice per SKU-less card | 25 = 234.95/hour | 25 = 234.96/hour | — |

Needs-seller entries: 18 (variant 8, over_stock 7, discount 1, card_state 1, other 1); 9 of them on a card
without SKU; 12 orders would otherwise have gone through. Reasons as the scorer prints them: `variant size #
not on card-#-# v#` 4, `size M` 2, `size L` 1, `card was draft when the comment was written` 1 (+1 combined
with a size), `card was retired when the comment was written` 1, discount 1, `qty # > stock #` 7.

Situations of the scorer on this run: 1 PASS 10/10 · 2 PASS 1/1 · 3 FAIL 8/28 · 4 FAIL 0/4 (stock question
answered 0/13) · 5 FAIL 1/2 · 6 PASS 2/2 · 7 PASS 1/1 · 8 PASS 1/1 · 9 PASS 4/4 · 10 NOT_TRIGGERED ·
11 TEST_ONLY · 12 PASS 2/2 — identical to the baseline.

Reading: column for column the merged main reproduces the baseline; the only differences are in the fourth
decimal of the per-hour rates (span rounding). This is not evidence that PR #61 changes nothing: the replay
session was never bound, so the media-time path was not exercised. Measuring the #61 effect on this
observation set needs a replay that bootstraps a binding for the session. The interventions remain scripted
shop actions over a 6.4 minute loop, not a measurement of a human seller, and stay far above 10/hour with or
without the projected SKU choices.

## Artifact files (sha256, first 16)

| File | sha256 |
| --- | --- |
| `rescore-main-1029-2128.json` (run file + score) | `8046764a2824efc7` |
| `rescore-main-1029-2128-scenarios.json` | `b0d6e1cdbb05963b` |
| `rescore-main-1029-2128-comments.jsonl` | `5519708fa5850e6c` |
| `rescore-main-1029-2128-metrics.json` (derived table above) | `7836065df6ab3726` |

The observation path inside the run file and the metrics file is reduced to `<outside-git>/…`; the hash of the
observation set is unchanged.
