# E2E on merged main — e2e-main-1030-1852 (dry) and e2e-main-1030-1852s (stand-in app)

Repeat of the 29 October check (`../2026-10-29/e2e-main-1029-2140-NOTE.md`) on current main, revision
`92ecf121f81becb499ba8c12de7d4e2dcdb2b9a3` (the merge of PR #66), tree `670775bff4b02bddc5cd7e74a2c3716f02434440`,
reached with `git fetch origin` and `git merge --ff-only origin/main` in a worktree of this repository. Same two
runs as last time: the in-process dry run and a stand-in run against a trial app on a free port, so that the SSE
close/reopen check and the worker/media proofs exist. Both sessions are unbound (no attachment event in either
session's files), i.e. the legacy order flow, as on 29 October. The index in `../README.md` is left untouched:
the 2026-10-29 evidence is not indexed there yet (README: "indexed separately once its branch lands"), so this
note is not indexed either.

| Item | Value |
| --- | --- |
| Model calls | 0 — `MODEL_API_KEY` is set to the empty string by `tests.db_lock` for every child process (trial app, runner, tests) and is not set in the operator's shell; `C5_OFFLINE` is not set by the operator, the runner sets it per loop (`offline_loop 1` in both proofs); stand-in proofs `model_key_set: false` twice (worker pid 8065, media pid 8067, in `run_file.loops[0].proofs` of the stand-in run file); offline line `model_client: "none: every call raises RuntimeError, as with no key"`; `model_call` rows in the test database after the runs: `(0 rows)` for `e2e-main-1030-1852` and `(0 rows)` for `e2e-main-1030-1852s` (SQL and answer printed by the runner in both runner logs, and once more in the ledger section of the SSE check log) — count 0 and 0 |
| `make check` on this revision | rc 0 — `All checks passed!`, `177 files already formatted`, `hygiene: clean`, `1473 passed in 414.32s (0:06:54)`; the summary line has no skipped or xfailed tests, so there is no skip reason to list |
| Database | `keviq_test` through the suite lock (`uv run python -m tests.db_lock -- …`); the worktree has no `.env`, so the test database was named through `TEST_DATABASE_URL` in the operator's shell (value not recorded here); the lock file was claimed and released by `tests.db_lock` only; ports 8000/8001 and the `keviq` database untouched |
| Dry run | `python -m measure.c5 --dry --loops 1 --no-code-check --run e2e-main-1030-1852 --out <out>` → rc 0; 466 events, seq 495262..495727 (comment_labeled 110, comment 100, order 77, speech 64, message_out 54, card 42, product_seen 12, message_in 4, price_proposal 3) |
| Stand-in run | trial app `app.main:app` on `127.0.0.1:8040` (`healthz` before: `{"ok":true,"store":"pg","head_seq":497183}`), `measure.c5 --base-url http://127.0.0.1:8040 --loops 1 --seconds 330 --run e2e-main-1030-1852s` → rc 0; 764 events, seq 497184..497947 (frame 330, comment_labeled 102, comment 92, order 71, speech 62, message_out 50, card 39, product_seen 11, message_in 4, price_proposal 3); the app was stopped afterwards and port 8040 is free |

The stand-in run was started twice. The first attempt ended with runner rc 1, `no frame from media in 120 s`:
the worktree's `data/local/test1/` had no recording (the recording is gitignored and was present only in the
main checkout). That attempt wrote 0 events under `e2e-main-1030-1852s`, so after linking the recording into the
worktree (still gitignored, not committed) the run was repeated with the same id; the files kept here are from
the repeated run only. Nothing else was retried.

## Commands and output

| Step | Command | Output |
| --- | --- | --- |
| 0 | `git fetch origin && git merge --ff-only origin/main && git rev-parse HEAD 'HEAD^{tree}'` | `92ecf121f81becb499ba8c12de7d4e2dcdb2b9a3` / `670775bff4b02bddc5cd7e74a2c3716f02434440` |
| 1 | `make check` | rc 0; `All checks passed!` · `177 files already formatted` · `hygiene: clean` · `1473 passed in 414.32s (0:06:54)` (`e2e-main-1030-1852-make-check.log`) |
| 2 | `uv run python -m tests.db_lock -- python -m measure.c5 --dry --loops 1 --no-code-check --run e2e-main-1030-1852 --out <scratch>/c5` | rc 0; `loop 1: done, retired 11, waits {'many': 1}, failures 0` · `(0 rows)` (`e2e-main-1030-1852-dry-runner.log`) |
| 3 | trial app `uvicorn app.main:app --host 127.0.0.1 --port 8040` under the lock, then `python -m measure.c5 --base-url http://127.0.0.1:8040 --loops 1 --seconds 330 --run e2e-main-1030-1852s --out <scratch>/c5`, four steps, SSE check, ledger count | runner rc 0; `loop 1: done, SETTLED/SETTLED behind 0, retired 10, waits {'many': 1}, failures 0` · `(0 rows)` · `model_call rows, session e2e-main-1030-1852s: 0` · `app stopped` (`e2e-main-1030-1852s-runner.log`, `e2e-main-1030-1852s-sse-check.log`) |
| 4a | `uv run python -m tests.db_lock -- env SLOW_VISION_REPORT=<scratch>/testc-compressed.jsonl uv run python -m pytest -q -s tests/test_slow_vision_integration.py --tb=short` | `5 passed in 77.37s (0:01:17)` (`e2e-main-1030-1852-testc-compressed.log`) |
| 4b | `uv run python -m tests.db_lock -- env SLOW_VISION_SPEED=1 SLOW_VISION_REPORT=<scratch>/testc-full-speed.jsonl uv run python -m pytest -q -s tests/test_slow_vision_integration.py --tb=short` | `5 passed in 415.24s (0:06:55)` (`e2e-main-1030-1852-testc-full-speed.log`) |
| after | `SELECT session_id, count(*) AS n, COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd FROM events WHERE type = 'model_call' AND session_id = '<run>' GROUP BY session_id;` for both ids | `(0 rows)` and `(0 rows)` |

The `cost before` / `cost after` lines that the stand-in script prints from the trial app's `/api/cost` are not in
the copied SSE check log: on the shared test database that endpoint aggregates every test session, not this run,
and the 29 October log had no such lines either. For the same reason `cost_api` inside the stand-in run file is
the shared-ledger answer at that moment, not a figure of this run; this run's own ledger rows are 0.

## Situations (the twelve of the scorer)

| # | Dry `e2e-main-1030-1852` | Stand-in `e2e-main-1030-1852s` | Scorer note |
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

Not 12 PASS: situations 3 and 4 fail on both runs, with the same counts as on 29 October. Code checks: dry run
NOT_RUN (`--no-code-check`); stand-in run 16 test files, every one rc 0 (`code_checks` in the run file).

### Compared with 29 October (`e2e-main-1029-2140` / `e2e-main-1029-2140s`)

| Measure | 29 October | 30 October | Changed |
| --- | --- | --- | --- |
| Situations 1–12, dry | 9 PASS, 2 FAIL (3: 16/28; 4: 0/4), 1 TEST_ONLY | 9 PASS, 2 FAIL (3: 16/28; 4: 0/4), 1 TEST_ONLY | no |
| Situations 1–12, stand-in | 9 PASS, 2 FAIL (3: 16/26; 4: 0/4), 1 TEST_ONLY | 9 PASS, 2 FAIL (3: 16/26; 4: 0/4), 1 TEST_ONLY | no |
| `make check` | `1359 passed, 2 xfailed in 70.19s` | `1473 passed in 414.32s` | count and time, result rc 0 both |
| Dry events / by type | 466; same distribution | 466; same distribution | no |
| Stand-in events / by type | 764; same distribution | 764; same distribution | no |
| Interventions, dry / stand-in | 22 / 20 | 22 / 20 | no |
| Needs-seller entries, dry / stand-in | 19 / 17 | 19 / 17 | no |
| Receipts at the confirmed version | 2/2 / 2/2 | 2/2 / 2/2 | no |
| Phone visible max, s, dry / stand-in | 0.055 / 0.038 | 0.064 / 0.043 | value; both far under 5 s |
| TEST-C compressed: hide latency, logical ms | 220 / 280 / 300 | 200 / 360 / 280 | value; all under 5000 |
| TEST-C compressed: vision done / skipped | 149 / 202 | 146 / 205 | value |
| TEST-C full speed: hide latency, logical ms | 13 / 10 / 12 | 20 / 22 / 21 | value |
| TEST-C full speed: vision done / skipped | 174 / 177 | 174 / 177 | no |
| TEST-C pytest time, compressed / full | 38.67 s / 377.13 s | 77.37 s / 415.24 s | value |
| Model calls, both runs | 0 | 0 | no |

No situation changed status; the only differences are timing values and the TEST-C vision done/skipped split of the
compressed run. No cause is claimed for them, none was measured.

## Four steps, by sequence number

Dry run, `e2e-main-1030-1852-four-steps.txt` (header `events 466 orders 27 confirmed 2`):

- `o-c5-1-016` (card-1-00, SP01): (1) price_proposal seq 495310 (heard 220000, current 250000, card_version 4) →
  (2) card v5 220000 seq 495316, edited by seller, caused by the proposal → DRAFT 495329, NEEDS_SELLER 495330
  (variant size 40 not on v5), CONFIRM_SENT 495336 (seller approved, unit price = card price) → (3) message_in
  `confirm_yes` 495357, CONFIRMED 495358 → (4) receipt message_out 495359 at card version 5; `version_exists True`,
  `price_matches_version True`, image False.
- `o-c5-1-029` (card-1-01, SP02): DRAFT 495392, NEEDS_SELLER 495393 (size 41 not on v3), CONFIRM_SENT 495400 →
  message_in 495416, CONFIRMED 495417 → receipt 495418 at v3 320000, price matches, image False.

Stand-in run, `e2e-main-1030-1852s-four-steps.txt` (header `events 764 orders 25 confirmed 2`): the same path with
seq 497287 (proposal), 497297 (card v5), 497323/497324/497335, 497360 (message_in), 497361 (CONFIRMED), 497362
(receipt v5) for `o-c5-1-016`, and 497418/497419/497430, 497452, 497453, 497454 (receipt v3) for `o-c5-1-029`.

Image on the receipt: NOT_RUN — the receipt has no image in either run (`image False`); no image check was made
and none is claimed.

## SSE close/reopen and health (stand-in app)

`e2e-main-1030-1852s-sse-check.log`, `e2e-main-1030-1852s-sse-mid.log`:

```
sse1: 764 ids, killed after 1 s, last id 497947
healthz at once: 200
healthz +5 s: 200
sse2 reopened with Last-Event-ID 497947: first id none, last id none (0 ids in 3 s)
healthz after: {"ok":true,"store":"pg","head_seq":497951}
sse3 reopened with Last-Event-ID 497641: first id 497642, last id 497947 (306 ids in 3 s)
same ids as the full stream after 497641 (no gap, no repeat): yes
contiguous (+1 each): yes
```

Reading: the full stream closed by the client and reopened after the last id repeats nothing (0 ids); the mid-cut
reopen (sse3) returns 306 ids from 497642 to 497947, the same ids as the full stream after the cut, with no gap
and no repeat. The app answered `/healthz` 200 at once and 5 s after the client closed the stream. Unlike on
29 October, the first pass (sse1/sse2) completed, so no repeat of the check was needed. `head_seq` 497951 after
the check is 4 above the last stream id: the rows in between belong to other sessions of the shared test database
(seq 497948 session `pytest-ver-…` type card, seq 497951 session `pytest-pg` type comment, looked up afterwards);
this session holds exactly 764 events, seq 497184..497947, as the ledger section of the SSE check log counts.

## TEST-C (`tests/test_slow_vision_integration.py`)

```
uv run python -m tests.db_lock -- env SLOW_VISION_REPORT=<scratch>/testc-compressed.jsonl uv run python -m pytest -q -s tests/test_slow_vision_integration.py --tb=short
5 passed in 77.37s (0:01:17)
uv run python -m tests.db_lock -- env SLOW_VISION_SPEED=1 SLOW_VISION_REPORT=<scratch>/testc-full-speed.jsonl uv run python -m pytest -q -s tests/test_slow_vision_integration.py --tb=short
5 passed in 415.24s (0:06:55)
```

SLOW_VISION summary (from the logs kept beside this note):

| | compressed (speed 20) | full speed (speed 1) |
| --- | --- | --- |
| frames / comments | 351 / 100 | 351 / 100 |
| digit phone hidden | 3/3 | 3/3 |
| hide latency, logical ms (c5-1-002 / 007 / 041) | 200 / 360 / 280 | 20 / 22 / 21 |
| model_calls | 0 | 0 |
| vision done / skipped (failed) | 146 / 205 (0) | 174 / 177 (0) |
| barrier | SETTLED, errors 0 | SETTLED, errors 0 |
| wall | 19.2 s | 354.2 s |

`drain` and `stopped` cases: SETTLED after the drain, INCOMPLETE kept while stopped — as the test expects.

## Other aggregates

| Measure | Dry | Stand-in |
| --- | --- | --- |
| Interventions, scripted | 22 = 208.91/hour | 20 = 199.61/hour |
| by cause | price_used 3, variant→CONFIRM_SENT 4, over_stock→CANCELLED 13, price_changed 1, discount 1 | price_used 3, variant 4, over_stock→CANCELLED 11, price_changed 1, discount 1 |
| Needs-seller entries | 19 (variant 4, over_stock 13, price_changed 1, discount 1); 15 would otherwise go through; 12 on a card without SKU | 17 (variant 4, over_stock 11, price_changed 1, discount 1); 13 would go through; 10 without SKU |
| Receipts | 2/2 at the confirmed version | 2/2 |
| Session minutes / phone visible max | 6.32 / 0.064 s (n 4) | 6.01 / 0.043 s (n 4) |
| Comments late max / speech late max | — | 10 ms / 7 ms (62 speech lines posted) |
| Worker vision (offline) | — | received 330, failed 330 (no model client), barrier SETTLED |

The rates are scripted shop actions over a 6.0–6.3 minute loop, not a human seller. The 330 failed vision tasks
of the stand-in worker are the expected offline behaviour (no model client); the barrier still reached SETTLED.

## Artifact files (sha256, first 16)

| File | sha256 |
| --- | --- |
| `e2e-main-1030-1852.json` | `f258817d3d43d922` |
| `e2e-main-1030-1852-scenarios.json` | `e1e74a3125d00ca1` |
| `e2e-main-1030-1852-comments.jsonl` | `198c3114e6494e69` |
| `e2e-main-1030-1852-four-steps.txt` | `8360b5f01502f883` |
| `e2e-main-1030-1852-dry-runner.log` | `ace74c6f7d6ef109` |
| `e2e-main-1030-1852-make-check.log` | `fc6f05c9ed12b0db` |
| `e2e-main-1030-1852-testc-compressed.log` | `b63b29c042f06f62` |
| `e2e-main-1030-1852-testc-full-speed.log` | `458b4a7455f61a08` |
| `e2e-main-1030-1852s.json` | `3be79bf11fc4075d` |
| `e2e-main-1030-1852s-scenarios.json` | `a37d8ae4333ad536` |
| `e2e-main-1030-1852s-comments.jsonl` | `fcbb809da102bed3` |
| `e2e-main-1030-1852s-runner.log` | `697ed3a17e030744` |
| `e2e-main-1030-1852s-four-steps.txt` | `142ca9002ba9815c` |
| `e2e-main-1030-1852s-sse-check.log` | `fa1465eeb80497f3` |
| `e2e-main-1030-1852s-sse-mid.log` | `81e2a37db2d5712d` |

Scratch, repository and home paths inside the logs are reduced to `<scratch>`, `<repo>` and `<home>`.
