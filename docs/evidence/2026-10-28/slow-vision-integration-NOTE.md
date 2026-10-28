# Slow vision integration: synthetic service times

The same test source reproduces the serial runner failure on `bf620a404efe520d9e83dc7439986d1d15626cea`
and passes the compressed checks on worker pull request #45, `0f08af47f4a3d0da1a3c2a9255a52f515c94dfb7`.
The test implementation is commit `44edbbe9d1ebc921bd2794e4012792ba23977adf`.

This is a 0 USD scheduling and shield regression. Vision results and label classification are synthetic;
the HTTP routes, PostgreSQL store, runner, shield, simulated moderator, progress reporter and barrier are real.
The raw synthetic comments are the committed `data/comments/c5/stream-1.jsonl`; all 100 are included.
It does not establish model recognition, word-phone detection, image quality, rendering, or C5 quality readiness.

## Results

| Check | Baseline | Worker #45 at 20:1 |
| --- | --- | --- |
| `Runner.stats()` available | FAIL: missing | PASS |
| Digit-phone first hide, DB-ingestion delta | FAIL: 15200 / 55380 / 242100 logical ms | PASS: 140 / 280 / 120 logical ms |
| Full input schedule | 351 frames / 100 comments posted | 351 frames / 100 comments processed |
| Vision outcome accounting | Unavailable | 149 done + 202 skipped = 351; no pending |
| Full-loop barrier | Unavailable | SETTLED; 711 delivered / 711 finished; no pending/queued/background |
| Barrier while vision is pending | Not tested in baseline command | INCOMPLETE |
| Worker stopped with outstanding frame | Not tested in baseline command | INCOMPLETE; pending=1 |
| Failure and stale-frame accounting | Not tested in baseline command | PASS |
| Switch-before-result / price-before-card | Deferred | Two strict expected failures |

`make check`: 517 passed, 7 skipped, 2 xfailed in 60.36 s; lint, formatting and quick fixture measurement passed.
The new integration cases executed on PostgreSQL. The quick measurement printed by `make check` uses fixtures,
and is not the full-loop fake-vision experiment or a model-quality result.

Full-speed (1:1): **5 passed, 2 xfailed in 364.50 s**. The complete timed profile took 354.392 s
including drain, with 351 frames and 100 labeled comments. All 3 digit-phone hides took 14 ms by stored ingestion
time; both fast control events finished within 11 ms. Vision outcomes: 175 done + 176 skipped = 351, no pending.
The full-loop barrier was SETTLED (737 delivered / 737 finished, background=queued=pending=0).
Both late comments have stored labels. The stopped-worker variant stayed INCOMPLETE with one pending frame.
No model_call events occurred. This offline result grants no permission for a paid run.

The accepted full-speed profile session is `slow-vision-7d24c4194472`. Its stored-event window is
2026-10-28T14:05:19.455000+07:00 through
2026-10-28T14:11:12.317000+07:00.
A read-only query found **0 event rows from other sessions** in that window. Source hashes remained unchanged
through validation. The command completed with exit 0. At readback a subsequent run already held the shared lock; its file was left untouched.

## Earlier run excluded from accepted timing

The first 1:1 profile ran approximately 13:50:24–13:56 local time. A concurrent test suite was reported on the
same database from approximately 13:54:40–13:55:28, inserting `pytest-*` events and holding an events-table lock.
Its timings are therefore retained as potentially contaminated diagnostics, not accepted measurements.
The initial test used a different local lock path, so it did not advertise ownership to other worktrees.

That earlier suite also had one barrier-check failure: in-process ASGI server tasks could be reported as worker
background work after the worker had drained. Test-only server-context tagging corrects that isolation problem.
The initial log, structured profile and source proof are preserved beside the accepted rerun evidence.
The accepted baseline and compressed results above use the shared lock; the new 1:1 rerun uses it as well.

## Exact clocks

The scheduled input covers 351.5 logical seconds, with one frame every logical second. The fake frame handler
alternates service times of 2 and 3 logical seconds. At speed 20 these are 0.05 s between frames and 0.10/0.15 s
per read. At speed 1 all three intervals are unchanged from their logical values.

Scaled in the test only: input waits, fake vision sleeps, VISION_TIMEOUT_S (10 / speed), BATCH_WAIT_S (1 / speed),
and progress reporter intervals (0.25 / speed and 1 / speed). No worker file is edited.

Unscaled: actual HTTP/DB execution, PostgreSQL `ingested_ms`, source `ts_ms` (the full 351500-ms media timeline),
`now_ms()`, barrier polling, its 2-second freshness rule, and the test's wall-time drain deadline.
Phone delay is `(first_hide.ingested_ms - comment.ingested_ms) * speed`, bounded by 5000 logical ms.
`latency_ms` is never used for this assertion. The simulator's hide record must also exist.

HOLD_MS remains 4168 in the timed replay and is not on its exercised handler path. The separate price-binding
expected failure sets it to zero to isolate selection of the old card while the new frame is still being read.
The 5-minute image guard is unchanged and untested: the fake handler emits no SAM image. A compressed run cannot
validate that wall-clock guard. These limits also apply to the full-speed run.

Both late comments are asserted in the database and every input comment must have a stored label:
`c5-1-095` at 335825 ms and `c5-1-098` at 339875 ms. The schedule is never truncated to 330 s.

## Transport and isolation

The client wraps `httpx.ASGITransport(app=create_app(PgStore(...)))`. ASGITransport buffers streaming bodies,
so it requests the app's existing SSE route with `follow=false`, then the unmodified runner reads again from its
cursor. The test covers app/store/runner integration, not TCP streaming, disconnects or backpressure.
Measurement-side polling and tasks created under the ASGI app context are excluded from the reporter’s
worker-background count, matching the separate server process in a network run. Worker lane and label tasks
remain counted. This is test-only context tagging; production progress code is unchanged. The real two-check barrier decides SETTLED versus INCOMPLETE.

The test checks the database name ends in `_test`, blanks MODEL_API_KEY, injects no model transport, and holds
the main checkout’s shared `.scratch/keviq_test.lock` for the whole pytest command, plus a PostgreSQL advisory
lock in its integration fixture. All worktrees resolve the common Git directory to find this same file. Child
fixtures reuse their parent command’s token. A foreign lock is not overwritten or removed; an owner releases
only its own file on exit. `make check` now takes this shared command-wide lock automatically. Sessions use unique `slow-vision-*` IDs.
No test rows are deleted. Main project rows, production processes, real comments and real Messenger are untouched.

## Reproduction

Use the project's test database configuration; its URL is omitted here. EVIDENCE_DIR is an operator-selected
scratch directory. Baseline uses the identical copied test file, verified by its SHA-256 below.

```sh
# Test worktree on worker #45 + the test commit; compressed, including make-check integration.
MODEL_API_KEY= SLOW_VISION_REPORT="$EVIDENCE_DIR/make-check.jsonl" make check

# Isolated bf620a4 worktree with the same test file; expect exactly two failures.
MODEL_API_KEY= DATABASE_URL="$TEST_DATABASE_URL" SLOW_VISION_REPORT="$EVIDENCE_DIR/baseline.jsonl" \
  python -m tests.db_lock -- python -m pytest -q -s tests/test_slow_vision_integration.py -k 'exposes_conserved or full_loop' --tb=short

# Test worktree, all seven cases at their original schedule speed.
MODEL_API_KEY= DATABASE_URL="$TEST_DATABASE_URL" SLOW_VISION_SPEED=1 \
  SLOW_VISION_REPORT="$EVIDENCE_DIR/full-speed.jsonl" \
  uv run python -m tests.db_lock -- pytest -q -s tests/test_slow_vision_integration.py --tb=short
```

Test source SHA-256: `80bf40897251a9484afbe7d86a10100eb80dbe15e68dffdec1a6cd2998fcde04`.
Accepted logs and complete structured frame outcomes are in [the evidence directory](slow-vision-integration/).
The [SHA256SUMS manifest](slow-vision-integration/SHA256SUMS) covers accepted results, isolation proof, source proof,
and the preserved earlier run. Its SHA-256 is `5fdc9f744be43e41b7a260d2c035f87462cf44977ee654f401444059e68e2e2b`.

| Log | SHA-256 |
| --- | --- |
| baseline.log | `500075c39dee458ff0f3f0db8a64cd45b6be5e75347831a95d87c307c21299ae` |
| make-check.log | `4701442a685c0d37c24f8ac575d66e0a48ffd842b34f5a27537c63693c35642b` |
| full-speed.log | `d40f92f1ad088bf31797d2681d615dcdef74b3a05614094723a226ccc45cda65` |

The initial potentially contaminated profile is explicitly named `initial-full-speed-contaminated.*` and is excluded
from the accepted timing above.
Independent review remains required. The two strict expected failures are retained for the media-time binding follow-up.
