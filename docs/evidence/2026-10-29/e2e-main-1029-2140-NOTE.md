# E2E on merged main — e2e-main-1029-2140 (dry) and e2e-main-1029-2140s (stand-in app)

Unpaid end-to-end check of the C5 scenario on main `f2c2dba` (the merge of PR #61) plus the measure-only
replay support of this branch, revision `7dc77430edf56ba3cec56cd77c9ce321c7dd8a35`. Two runs: the in-process
dry run that the checklist names, and a stand-in run against a trial app on a free port so that the SSE
close/reopen check and the worker/media proofs exist. Both sessions are unbound (no attachment binding),
i.e. the legacy order flow; the bound rescore is reported separately (`rescore-bind-1029-*-NOTE.md`).

| Item | Value |
| --- | --- |
| Model calls | 0 — `MODEL_API_KEY` blank (set to the empty string by `tests.db_lock` and the scripts); stand-in proofs `model_key_set: false` twice (worker and media process); `model_call` rows: `(0 rows)` for both sessions (SQL printed by the runner) |
| `make check` on this revision | rc 0 — `All checks passed!`, `1359 passed, 2 xfailed in 70.19s (0:01:10)` |
| Database | `keviq_test` through the suite lock (`uv run python -m tests.db_lock -- …`); ports 8000/8001 and the `keviq` database untouched |
| Dry run | `python -m measure.c5 --dry --loops 1 --no-code-check --run e2e-main-1029-2140 --out <out>` → rc 0; 466 events, seq 206934..207399 (comment 100, comment_labeled 110, order 77, speech 64, message_out 54, card 42, product_seen 12, message_in 4, price_proposal 3) |
| Stand-in run | trial app `app.main:app` on `127.0.0.1:8040`, `measure.c5 --base-url http://127.0.0.1:8040 --loops 1 --seconds 330 --run e2e-main-1029-2140s` → rc 0; 764 events, seq 208859..209622 (frame 330, comment_labeled 102, comment 92, order 71, speech 62, message_out 50, card 39, product_seen 11, message_in 4, price_proposal 3) |

## Situations (the twelve of the scorer)

| # | Dry `e2e-main-1029-2140` | Stand-in `e2e-main-1029-2140s` | Scorer note |
| --- | --- | --- | --- |
| 1 | PASS 10/10 | PASS 10/10 | items with a card per loop |
| 2 | PASS 3/3 | PASS 3/3 | proposals on a card that already had a price |
| 3 | FAIL 16/28 (0.571; previous_card 4/4; no card 0) | FAIL 16/26 (0.615) | target 0.9 on n ≥ 50 |
| 4 | FAIL 0/4 | FAIL 0/4 | ask_variant ≤ 5 s; stock question answered 0/13 |
| 5 | PASS 2/2 | PASS 2/2 | late comments tied to the card shown when written |
| 6 | PASS 1/1 | PASS 1/1 | a price wait happened (existence only) |
| 7 | PASS 1/1 | PASS 1/1 | "do not cancel" leaves the order open |
| 8 | PASS 1/1 | PASS 1/1 | CANCEL quick reply → CANCELLED |
| 9 | PASS 4/4 | PASS 4/4 | spelled-out phone hidden within 5 s |
| 10 | PASS 1/1 | PASS 1/1 | "ok" after the card changed → NEEDS_SELLER, confirm again |
| 11 | TEST_ONLY | TEST_ONLY | schema / 429: tests only |
| 12 | PASS 2/2 | PASS 2/2 | CONFIRMED with the receipt at the confirmed version |

Not 12 PASS: situations 3 and 4 fail on both runs, as on the 28 October diag and the two rescores. They
are reference accuracy and the stock question, both unchanged by this branch (measure-only). Code checks:
dry run NOT_RUN (`--no-code-check`); stand-in run 16 test files, every one rc 0 (`code_checks` in the run file).

## Four steps, by sequence number

Dry run, `e2e-main-1029-2140-four-steps.txt`:

- `o-c5-1-016` (card-1-00, SP01): (1) price_proposal seq 206982 → (2) card v5 220000 seq 206988, edited by
  seller, caused by the proposal → DRAFT 207001, NEEDS_SELLER 207002 (variant size 40 not on v5),
  CONFIRM_SENT 207008 (seller approved, unit price = card price) → (3) message_in `confirm_yes` 207029,
  CONFIRMED 207030 → (4) receipt message_out 207031 at card version 5; `version_exists True`,
  `price_matches_version True`, image False.
- `o-c5-1-029` (card-1-01, SP02): DRAFT 207064, NEEDS_SELLER 207065 (size 41 not on v3), CONFIRM_SENT 207072 →
  message_in 207088, CONFIRMED 207089 → receipt 207090 at v3 320000, price matches, image False.

Stand-in run, `e2e-main-1029-2140s-four-steps.txt`: the same path with seq 208962 (proposal), 208972 (card v5),
208998/208999/209010, 209035 (message_in), 209036 (CONFIRMED), 209037 (receipt v5) for `o-c5-1-016`, and
209093/209094/209106, 209127, 209128, 209129 (receipt v3) for `o-c5-1-029`.

Image on the receipt: NOT_RUN — the receipt has no image in either run (`image False`); no image check was
made and none is claimed.

## SSE close/reopen and health (stand-in app, TEST-C candidate lines)

`e2e-main-1029-2140s-sse-check.log`, `e2e-main-1029-2140s-sse-mid.log`:

```
sse3: 764 ids, killed after 1 s, last id 209622
healthz at once: 200
healthz +5 s: 200
sse4 reopened with Last-Event-ID 209622: first id none, last id none (0 ids in 3 s)
sse5 reopened with Last-Event-ID 209000: first id 209001, last id 209622 (622 ids in 3 s)
contiguous: yes
```

Reading: a full stream closed by the client and reopened after the last id repeats nothing (0 ids), which
is the wanted behaviour but on its own does not distinguish "resumed correctly" from "resumed nothing";
the mid-cut reopen (sse5) does: 622 ids from 209001 to 209622 with no gap and no repeat. The app answered
`/healthz` 200 at once and 5 s after the client closed the stream. The first attempt in `e2e-run.sh`
(sse1/sse2) stopped at the sse2 line because `grep -c` returned 1 on 0 ids under `set -euo pipefail`; the
runner result (rc 0) was already written, and the check was repeated as sse3–sse5 above.

## TEST-C (`tests/test_slow_vision_integration.py`)

```
uv run python -m tests.db_lock -- env SLOW_VISION_REPORT=<scratch>/testc-compressed.jsonl uv run python -m pytest -q -s tests/test_slow_vision_integration.py --tb=short
5 passed in 38.67s
uv run python -m tests.db_lock -- env SLOW_VISION_SPEED=1 SLOW_VISION_REPORT=<scratch>/testc-full-speed.jsonl uv run python -m pytest -q -s tests/test_slow_vision_integration.py --tb=short
5 passed in 377.13s (0:06:17)
```

SLOW_VISION summary (from the logs kept beside this note):

| | compressed (speed 20) | full speed (speed 1) |
| --- | --- | --- |
| frames / comments | 351 / 100 | 351 / 100 |
| digit phone hidden | 3/3 | 3/3 |
| hide latency, logical ms | 220 / 280 / 300 | 13 / 10 / 12 |
| model_calls | 0 | 0 |
| vision done / skipped | 149 / 202 | 174 / 177 |
| barrier | SETTLED, errors 0 | SETTLED, errors 0 |
| wall | 19.2 s | 353.6 s |

`drain` and `stopped` cases: SETTLED after the drain, INCOMPLETE kept while stopped — as the test expects.

## Other aggregates

| Measure | Dry | Stand-in |
| --- | --- | --- |
| Interventions, scripted | 22 = 208.89/hour | 20 = 199.59/hour |
| by cause | price_used 3, variant→CONFIRM_SENT 4, over_stock→CANCELLED 13, price_changed 1, discount 1 | price_used 3, variant 4, over_stock→CANCELLED 11, price_changed 1, discount 1 |
| Needs-seller entries | 19 (variant 4, over_stock 13, price_changed 1, discount 1); 15 would otherwise go through; 12 on a card without SKU | 17 (variant 4, over_stock 11, …); 13 would go through; 10 without SKU |
| Receipts | 2/2 at the confirmed version | 2/2 |

The rates are scripted shop actions over a 5.5–6.3 minute loop, not a human seller.

## Artifact files (sha256, first 16)

| File | sha256 |
| --- | --- |
| `e2e-main-1029-2140.json` | `c62c528f8c787676` |
| `e2e-main-1029-2140-scenarios.json` | `65e1d1363ecd7210` |
| `e2e-main-1029-2140-comments.jsonl` | `20b04fce1a46eb3f` |
| `e2e-main-1029-2140-four-steps.txt` | `791c423882079f2f` |
| `e2e-main-1029-2140-testc-compressed.log` | `f85d78c49a40fa48` |
| `e2e-main-1029-2140-testc-full-speed.log` | `f9f6197c968daf7a` |
| `e2e-main-1029-2140s.json` | `a6b8cf5e715254d1` |
| `e2e-main-1029-2140s-scenarios.json` | `79fa1bae557d4685` |
| `e2e-main-1029-2140s-comments.jsonl` | `0e95ec18d21cf8f1` |
| `e2e-main-1029-2140s-runner.log` | `9fac343921c48289` |
| `e2e-main-1029-2140s-four-steps.txt` | `6463f368787b9045` |
| `e2e-main-1029-2140s-sse-check.log` | `c9e73916711a393a` |
| `e2e-main-1029-2140s-sse-mid.log` | `4c76380517706aa6` |

Scratch paths inside the logs are reduced to `<scratch>`.
