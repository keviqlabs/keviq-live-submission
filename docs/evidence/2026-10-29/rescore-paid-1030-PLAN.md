# Plan for the paid bound rescore — rescore-paid-1030 (NOT RUN)

A plan, not a result. Nothing paid below has been executed: no model call. The 0 USD preparations of
29 October (ledger database, two dry runs, the wrapper file) are in the last section. It is the run that the cost appendix of `rescore-bind-1029-2218-NOTE.md` estimated (scenario C,
about 2.7 USD, bounded by the 8 USD session cap), written so that the go decision can be taken on the exact
commands. Everything is derived from the code at revision `d8c7f5e` (`measure/c5_live.py`, `measure/c5_bind.py`,
`measure/c5_proc.py`, `app/attachments.py`, `worker/runner.py`, `worker/attachments.py`, `app/migrate.py`,
`tests/db_lock.py`, `meta/meter.py`) and from the logs of the unpaid run `rescore-bind-1029-2218`.

| Item | Plan |
| --- | --- |
| Run id | `rescore-paid-1030-<HHMM>` (the session id of the trial app) |
| Revision | the branch head at run time (`measure/`, `tests/`, `docs/evidence/` only differ from main `f2c2dba`) |
| Model calls | yes: worker vision reads (Spark), card cut-outs (SAM), comment labels, media transcribe; one loop, 350 s of frames at 1 fps |
| Cap | `MODEL_SESSION_CAP_USD=8`, `MODEL_SESSION_WARN_USD=6` on the app and on the runner (the runner refuses to start if the app's `/api/cost?session=<run>` reports different values, `measure/c5_live.py:377-379`) |
| Who runs it | the branch owner, from this worktree; the wrapper below exports `MODEL_API_KEY` read from the main checkout's `.env` into its own process (`settings.load_dotenv` does not replace an exported value, and this worktree's `.env` has no key line) and never prints it |

## (a) Which database keeps the `model_call` ledger

The run cannot go through `tests.db_lock`: that wrapper sets `MODEL_API_KEY=""` for its child
(`tests/db_lock.py`), so a paid run under it makes no calls. Without the lock, `keviq_test` is the database the
test suite resets while it runs, so the ledger of a paid run must not live there either. The proposal is a
third database on the same PostgreSQL server (the compose container `keviq-live-postgres-1`, port 55433):

- Name: `keviq_paid_test`. The suffix `_test` is what `app.migrate --test` and `app.settings.is_test_database`
  require (`app/migrate.py:53`); the test suite only ever uses `keviq_test`, so nothing resets this one.
- Create and migrate, nothing else touched (`ensure_database` connects to the `postgres` maintenance database on
  the same server to issue `CREATE DATABASE`; the Founder's `keviq` database is not opened):

```
DBURL="$(sed -n 's/^DATABASE_URL=//p' .env | tr -d '"')"   # the compose URL of .env, never printed
TEST_DATABASE_URL="${DBURL%/*}/keviq_paid_test" uv run python -m app.migrate --test
```

  Expected output: `created the test database` then `migrations applied: 001_events.sql, 002_attachments.sql,
  003_attachment_inputs.sql` (the three files of `migrations/`).
- The trial app and the runner both get `DATABASE_URL=<that same URL>`; the runner's final SQL
  (`c5.finish`, which prints the `model_call` sum for the session) reads the same URL.
- Caps: the app computes `today_usd` / `total_usd` for the daily and total caps (30 / 300 USD) from its own
  database (`app/store.py` `costs()`, read by `meta/meter.py` `http_spend` through `/api/cost`). In
  `keviq_paid_test` both start at 0, so the daily and total caps are not a bound for this run; the session cap
  (8 USD, all time for the session id) is the bound that holds, and it is enforced in the same place
  (`http_session_spend`). The Founder's ledger in `keviq` (9.80 USD total, 0 on 29 October per the SQL in the
  2218 appendix) is added by hand in the NOTE after the run: `keviq` total + this run's session total.
- After the run the `model_call` rows are exported to a file beside the NOTE (`<run>-model-calls.jsonl`,
  hash in the NOTE) with this SELECT, so that the ledger survives without copying rows into `keviq`:

```
SELECT seq, event_id, ts_ms, body->>'purpose' AS purpose, body->>'model' AS model,
       (body->>'cost_usd')::numeric AS usd, (body->>'latency_ms')::int AS latency_ms,
       body->>'ok' AS ok, body->>'error' AS error
FROM events WHERE type = 'model_call' AND session_id = '<run>' ORDER BY seq;
```

Not chosen: `keviq` itself (the Founder's running database, read-only for this branch; a trial app on it would
also share its ledger with the live app's caps), and `keviq_test` (reset by the suite).

## (b) The wrapper command

The 2218 wrapper rewritten for the paid run; the full text is below and the file `rescore-paid-run.sh`
(sha256 `31f7dee5d31ef812d2cf0c117238fb307317ed6f13fc27d204a37ff2ee03c053`) is committed beside the run's evidence after the run. One stage per call:
`bash rescore-paid-run.sh <run> <out> <seconds> [port]`. Differences from 2218: the model key is read from the
main checkout's `.env` into the wrapper process only (never printed, never written; the worktree's own `.env`
has no key line and `load_dotenv` keeps an exported value); `--paid` replaces `--observations`; `--seconds` is
an argument; `TEST_DATABASE_URL` is set to the ledger URL, because `app/settings.py` `testing_database_url()`
appends `_test` to `DATABASE_URL` when it is unset and `app.migrate --test` would otherwise create
`keviq_paid_test_test`; the app writes its own INFO lines (needed for (c), see there); the image guard's window
is the stage length, at most 300 s; `C5_OFFLINE` is unset before the runner starts. The four binding variables
are generated into shell variables and exported; no value is printed, the log shows only the session id and the
epoch (`measure/c5_bind.py` `public()` exposes session, epoch and principal). The runner refuses to start if
`C5_BIND_SESSION` differs from `--run` (`measure/c5_live.py:362-363`).

```bash
#!/bin/bash
# Paid bound rescore, one stage per call: rescore-paid-run.sh <run> <out> <seconds> [port]
#   stage 1: <run>=rescore-paid-1030-<HHMM>  seconds=120
#   stage 2: a new run id, seconds=350, only when stage 1 had a `covered=True` line or a comment_attachment
#            that is not expired.
# The model key is read from the main checkout's .env into this process only: never printed, never written.
# Both tokens of the binding are generated here and never printed. The ledger database is keviq_paid_test.
set -uo pipefail
RUN=$1; OUT=$2; SECS=$3; PORT=${4:-8040}
BASE=http://127.0.0.1:$PORT
MAIN_ENV=${MAIN_ENV:?set MAIN_ENV to the path of the main checkout .env}
mkdir -p "$OUT"
[ -f measure/c5_bind.py ] || { echo "run from the worktree root"; exit 1; }
[ -f "$OUT/log-config.json" ] || { echo "missing $OUT/log-config.json"; exit 1; }
case "$RUN" in rescore-paid-*) ;; *) echo "run id must start with rescore-paid-"; exit 1;; esac
DBURL="$(sed -n 's/^DATABASE_URL=//p' .env | tr -d '"' | tr -d "'")"
export DATABASE_URL="${DBURL%/*}/keviq_paid_test"
export TEST_DATABASE_URL="$DATABASE_URL"
echo "DATABASE_URL name: /${DATABASE_URL##*/}"
export MODEL_API_KEY="$(sed -n 's/^MODEL_API_KEY=//p' "$MAIN_ENV" | tr -d '"' | tr -d "'")"
echo "MODEL_API_KEY set: $([ -n "$MODEL_API_KEY" ] && echo yes || echo no)"
[ -n "$MODEL_API_KEY" ] || { echo "no model key: refusing a paid run"; exit 1; }
export MODEL_SESSION_CAP_USD=8 MODEL_SESSION_WARN_USD=6 CATALOG_PATH=data/comments/c5/catalog.json
unset C5_OFFLINE
export C5_BIND_SESSION="$RUN" C5_BIND_EPOCH="epoch-$RUN"
C5_BIND_SOURCE_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
C5_BIND_SYSTEM_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
export C5_BIND_SOURCE_TOKEN C5_BIND_SYSTEM_TOKEN
echo "binding: session $C5_BIND_SESSION epoch $C5_BIND_EPOCH (two tokens generated, not shown)"
uv run python -m app.migrate --test || exit 1
if curl -s --max-time 1 "$BASE/healthz" >/dev/null 2>&1; then echo "port $PORT already answers; refusing"; exit 1; fi
uv run uvicorn measure.c5_bind:app --host 127.0.0.1 --port "$PORT" --log-config "$OUT/log-config.json" > "$OUT/$RUN-app.log" 2>&1 &
APP=$!
for i in $(seq 1 60); do curl -s --max-time 1 "$BASE/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
echo "healthz: $(curl -s --max-time 2 "$BASE/healthz")"
COST_BEFORE="$(curl -s --max-time 2 "$BASE/api/cost?session=$RUN")"
echo "cost before: $COST_BEFORE"
if ! python3 - "$COST_BEFORE" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
got = (d.get("session_usd"), d.get("session_cap_usd"), d.get("session_warn_usd"))
ok = got == (0.0, 8.0, 6.0)
print("cost pre-flight", "ok" if ok else f"WRONG {got}")
sys.exit(0 if ok else 1)
PY
then kill $APP 2>/dev/null; wait $APP 2>/dev/null; echo "app stopped, nothing run"; exit 1; fi
uv run python -m measure.c5 --base-url "$BASE" --loops 1 --seconds "$SECS" --paid --run "$RUN" --out "$OUT" > "$OUT/$RUN-runner.log" 2>&1 &
RUNNER=$!
WINDOW=$SECS; [ "${WINDOW%.*}" -gt 300 ] && WINDOW=300
uv run python docs/evidence/2026-10-28/scripts/paid-image-5min.py --base-url "$BASE" --run "$RUN" --window-s "$WINDOW" > "$OUT/$RUN-image-guard.log" 2>&1 &
GUARD=$!
wait $RUNNER; rc=$?
echo "runner rc=$rc"
if kill -0 $GUARD 2>/dev/null; then
  kill $GUARD 2>/dev/null; wait $GUARD 2>/dev/null; echo "guard: still polling at runner exit (stopped): no IMAGE_OK within the run"
else
  wait $GUARD; echo "guard rc=$?"
fi
grep -E 'IMAGE_OK|IMAGE_MISSING' "$OUT/$RUN-image-guard.log" | head -3
grep -v '^wrote ' "$OUT/$RUN-runner.log" | head -30
echo "cost after: $(curl -s --max-time 2 "$BASE/api/cost?session=$RUN")"
echo "state after: $(curl -s --max-time 2 "$BASE/api/sessions/$RUN/state" | python3 -c 'import sys,json; d=json.load(sys.stdin); print({k: d.get(k) for k in ("attachment_enabled",)})')"
echo "app log: attachments INFO lines $(grep -c ' attachments INFO ' "$OUT/$RUN-app.log"), covered=True $(grep -c 'covered=True' "$OUT/$RUN-app.log")"
kill $APP 2>/dev/null; wait $APP 2>/dev/null
echo "app stopped"
exit $rc
```

`$OUT/log-config.json`, written before the start (root logger at INFO so that the `app` logger's lines reach
the app log; uvicorn's own access lines keep their handler):

```
{"version": 1, "disable_existing_loggers": false,
 "formatters": {"line": {"format": "%(asctime)s %(name)s %(levelname)s %(message)s"}},
 "handlers": {"err": {"class": "logging.StreamHandler", "formatter": "line", "stream": "ext://sys.stderr"}},
 "root": {"handlers": ["err"], "level": "INFO"}}
```

Why: in the 2218 app log there are 0 `attachment evaluation` lines for 211 readiness posts — `app/main.py`
does not configure logging and uvicorn's default config leaves the root logger at WARNING, so the INFO line of
`app/attachments.py:444` was never written. The dict above was checked against a running app on 29 October (last section): the app's only INFO site
is `app/attachments.py:444`, logger `attachments`; the `app` logger of `app/main.py` has no INFO line, so the
count to watch is `attachments INFO`, and a healthz-only app writes none of them.

What the runner then verifies by itself before any model call (`measure/c5_live.py:354-395`): cap/warn equal
on app and runner; the session is empty; `attachment_enabled` is true for the bound session; the worker child's
proof line says `model_key_set: true` and carries an `attachment` line (`measure/c5_proc.py`); no `offline` line
(`C5_OFFLINE` is only set when unpaid, and the child refuses `C5_OFFLINE` with a key); the media child runs
without `--no-voice`. The 2218 run file shows the same proof fields with `model_key_set: false`.

What stops a run at once: the runner `SystemExit` on any of the above; a `model_call` event whose `error` starts
with `session_cap` (`Watch.cap`, loop ends, children stopped, `capped` recorded in the run file); SIGTERM to the
`measure.c5` process (children end through `Child.stop` / `Child.term`, logs are still written, the app is
stopped by the wrapper). Operator rule (d) uses the last one.

## (c) Telling "expired for a missing read" from "expired for timing"

Facts of the code that the split rests on:

- The worker posts readiness for a comment only when every required frame (ts ≤ comment ts) has a terminal
  record and a later frame exists (`worker/attachments.py:136-143`); a frame that was skipped or stale in the
  vision lane never gets one (queue replacement is not a read, `contracts/comment_attachment.md:259`), so for
  such a comment no readiness post is ever made: that is **missing read**.
- A frame's terminal time: for `product_seen`, when its cards were posted (`product_finished`,
  `worker/attachments.py:72-84`; the card events' `ingested_ms` in the database, the read itself in the
  `product_seen` event's `timing.infer_start_ms` / `infer_end_ms`); for a read that found nothing, when its
  handlers returned (no event, no log line); for a timeout, the worker log line `handlers of <frame id>
  cancelled after 10.0 s` (`worker/runner.py:221`, terminal `read_error`).
- The app evaluates a readiness post only while `now < deadline` (`deadline = comment.ingested_ms + 4000`) and
  then writes `attachment evaluation: covered=<bool> resolved=<bool> remaining_ms=<int>`
  (`app/attachments.py:444`); a post after the deadline writes the expiry without that line. A post with
  `covered=True resolved=True` can still end as `expired` when the clock passes the deadline between the line
  and the store (`app/attachments.py:478-494`, `AttachmentDeadlinePassed`): that is **timing**, at the app.
- The expiry event keeps `comment_ingested_ms`, `deadline_ms`, `snapshot_at_ms` and `reason`
  (`original attachment deadline expired`, or `recovery-late: …` when written more than 200 ms late).

Per expired order comment C, from the database (`DATABASE_URL` of the run) and the two logs:

1. Required frames R(C): `SELECT event_id, ts_ms, ingested_ms FROM events WHERE type='frame' AND
   session_id='<run>' AND ts_ms <= <comment ts_ms>` (plus the first frame after, which must exist).
2. For each frame in R(C), its status, by id: `skipped` / `stale` from the worker log lines
   `frame <id> skipped|stale (session …)` (`worker/runner.py:253`); `timeout` from `handlers of <id>
   cancelled after 10.0 s`; `product_seen` from the database (`product_seen` events whose `caused_by` holds the
   frame id; their card events give the terminal time); the rest were read with no product, their end time is
   not recorded.
3. Classification, first rule that matches:
   - **missing read** — some frame in R(C) is `skipped` or `stale`, or is still waiting / in flight at
     `deadline_ms` (the frame's `ingested_ms` + a read that cannot end before the deadline: frames whose
     `product_seen` `infer_end_ms` ≥ `deadline_ms`, or a timeout line after it). No readiness post for C can
     exist before the deadline; cross-check: no `attachment evaluation` line between C's `comment_ingested_ms`
     and `deadline_ms` attributable to C (attribution below).
   - **timing** — every frame in R(C) has a terminal before `deadline_ms` or is a timeout/no-product read, and
     either (i) an `attachment evaluation: covered=True resolved=True remaining_ms=<n>` line attributable to C
     exists with a small `n` and the attachment is still `expired` (the store re-check turned it), or (ii) no
     evaluation line exists before the deadline but the terminal of the last required frame (its cards'
     `ingested_ms`, or its `infer_end_ms`) is within the window: the worker's post (history read
     `observe_through`, then the POST) reached the app after the deadline. Both are "the read finished, the
     evidence was late": the number to report is the gap `last terminal − deadline_ms` (negative in (i),
     positive in (ii)) and the read length `infer_end_ms − infer_start_ms` of the frame that decided it (the
     2.4 s figure of the diag).
   - **unresolved** — an evaluation line attributable to C with `resolved=False` (the reference could not be
     resolved from the snapshot; neither read nor timing).
   - **evidence rejected** — an evaluation line with `covered=False` although every frame in R(C) is terminal:
     `check_evidence` refused the records (`app/attachments.py:309-370`, for example a `read_error` on a frame
     cited by a stored `product_seen`, line 358 — the 2218 cause). Expected 0 in a paid run; if not 0 it is a
     finding of its own.
   - **ready** — not expired (the count that matters for the reference measure).
4. Attribution of an `attachment evaluation` line to a comment: the line carries no comment id, so a line at
   wall time t is given to the order comment whose window `[comment_ingested_ms, deadline_ms)` contains t
   (the app log timestamps are wall ms, the same clock as `ingested_ms`). Windows of two order comments can
   overlap (30 order comments over 350 s, 4 s each); a line inside two open windows is counted as
   **ambiguous** and reported as such, not guessed. The uvicorn access line of the same POST follows the
   evaluation line (the handler logs before the response), `202 Accepted` = pending, `200 OK` = an attachment
   event was written.

The NOTE after the run reports the five counts (`missing read`, `timing`, `unresolved`, `evidence rejected`,
`ready`) plus `ambiguous`, with `n = 30` order comments, and for the `timing` group the median and maximum of
`last terminal − deadline_ms` and of the read length. The classification is done by a script beside the NOTE
(`<run>-classify.py`, inputs: the SELECT above, `<run>-app.log`, `<run>-loop1-worker.log`), committed with the
evidence; it is not written yet.

## (d) Early stop

The first 10 order comments of the loop are `c5-1-001, 006, 010, 016, 018, 021, 024, 029, 031, 032`
(`rescore-bind-1029-2218-comments.jsonl`, same scenario); in 2218 their deadlines fell within the first
≈ 115 s of the loop. The operator watches two things while the loop runs:

```
watch -n 5 'grep -c "attachment evaluation" <out>/<run>-app.log; \
            grep -c "covered=True" <out>/<run>-app.log; \
            curl -s http://127.0.0.1:8040/api/cost?session=<run>'
```

and, every 10 s, the session's expired count:

```
psql "$DATABASE_URL" -At -c "SELECT body->>'outcome', count(*) FROM events WHERE type='comment_attachment'
  AND session_id='<run>' GROUP BY 1;"
```

Stop rule: when 10 `comment_attachment` events exist and all 10 are `expired` and the app log has no
`covered=True` line, send `SIGTERM` to the `measure.c5` process (`kill $RUNNER` in the wrapper's shell; the
runner stops its children, the wrapper stops the app). Expected spend at that point: about 0.15 USD (vision
reads of ≈ 115 frames at ≈ 0.0027 USD plus labels of ≈ 30 comments). The partial run keeps its run file and
logs; the NOTE then records the 10 classifications of (c) and the ledger, and names the run `stopped early`.

Also stop, regardless of the attachment count: a `session_cap` refusal (the runner does this itself); the
session reaching the 6 USD warning before 200 s of the loop (`/api/cost?session=<run>` `session_usd`); any
`401` or `422` on `/attachments/system` in the app log (binding or evidence contract broken, the 2218 run had
none); the worker progress lines showing vision `failed` above half of `received` (reads timing out at 10 s,
`C5PROC … "progress"` lines in the runner log); the worker proof line without `model_key_set: true`.

Do not stop for: situations 3 and 4 failing (expected, unchanged by this branch); NEEDS_SELLER two-candidate
drafts (the media-time path working as specified); `422` on `/orders/<id>/decision` from the scripted shop
(the 2218 run had 7, the shop's scripted decision does not fit a two-candidate draft; counted, not a stop).

## (e) Two stages, one go line

Stage 1: run id `rescore-paid-1030-<HHMM>`, `--seconds 120`: the first 10 order comments (`c5-1-001` to
`c5-1-032`, deadlines within ≈ 115 s of the loop in 2218) get rule (d) on a 2 minute loop. Stage 2: a new run
id (the runner requires an empty session), `--seconds 350`, started only when stage 1 has at least one
`covered=True` line in its app log or at least one `comment_attachment` whose `outcome` is not `expired`:

```
grep -c 'covered=True' <out>/<stage 1>-app.log
psql "$DATABASE_URL" -At -c "SELECT body->>'outcome', count(*) FROM events WHERE type='comment_attachment'
  AND session_id='<stage 1>' GROUP BY 1;"
```

When stage 1 has all 10 expired and no `covered=True` line: stop, no stage 2; the NOTE records the 10
classifications of (c) and the stage's cost. The go line names stage 1's run id and stage 2's condition; stage
2 is one more wrapper call with the new run id.

## (f) Operator, ledger, NOTE, revision

- Operator: the branch owner, from this worktree, with the wrapper of (b). The key is read by the wrapper from
  the main checkout's `.env` into the wrapper process; no command prints it (`MODEL_API_KEY set: yes|no` only),
  no file receives it, the NOTE does not carry it.
- `keviq_paid_test` is kept after the run until the NOTE is accepted; no `DROP DATABASE` before that.
- The NOTE after the run: the five numbers of (c) (`missing read`, `timing`, `unresolved`, `evidence
  rejected`, `ready`) plus `ambiguous`, over n = the order comments whose deadline fell inside the loop window
  (not the scenario's 30); the session's `model_call` events as a jsonl file beside the NOTE with its sha256;
  total cost = `SUM(cost_usd)` of the session's `model_call` rows in the ledger (the runner's final SQL), to be
  set against the provider's billing page as photographed by the Founder after the run.
- `C5PROC` sha in the run file = the head of this branch, not main; accepted because `app/`, `worker/`,
  `orders/`, `meta/`, `contracts/` and `migrations/` on this branch equal main `f2c2dba`.

## Go checklist (one command or one checkable sentence per item)

1. **Ledger database.** `keviq_paid_test` on the compose server, not `keviq_test`, not `keviq`. Create and migrate:
   `TEST_DATABASE_URL="$DATABASE_URL" uv run python -m app.migrate --test` with `DATABASE_URL` = the `.env` URL
   with its database name replaced by `keviq_paid_test` — done on 29 October (last section); the wrapper runs it
   again and prints `migrations applied: none (up to date)`, as the dry run did. The trial app and the
   runner read `DATABASE_URL` = that URL (exported once in the wrapper; the first wrapper line prints the name
   `keviq_paid_test`). Pre-run check, done by the wrapper and fatal when it fails:
   `curl -s http://127.0.0.1:8040/api/cost?session=<run>` must contain `"session_usd": 0.0`, `"session_cap_usd": 8.0`,
   `"session_warn_usd": 6.0` (line `cost pre-flight ok`), and `psql "$DATABASE_URL" -At -c "SELECT count(*) FROM events WHERE session_id='<run>'"` prints `0`.
2. **No `tests.db_lock`.** The runner is started directly by the wrapper (`uv run python -m measure.c5 …`); the 2218
   wrapper line `uv run python -m tests.db_lock -- bash …` is not used, since the lock wrapper blanks
   `MODEL_API_KEY` and points `DATABASE_URL` at `keviq_test`. The runner carries `MODEL_SESSION_CAP_USD=8
   MODEL_SESSION_WARN_USD=6` in its own environment (exported before both the app and the runner), and refuses to
   start when the app reports other values. No part of the run needs the `keviq_test` lock: the trial app, the
   worker, the media process and the runner's final SQL all use `keviq_paid_test`, and `.scratch/keviq_test.lock`
   is neither taken nor touched. Checkable: `grep -c db_lock rescore-paid-run.sh` prints `0`.
3. **Port.** Trial app on `127.0.0.1:8040` only; the wrapper refuses when 8040 already answers `/healthz`; nothing
   on `:8000` or `:8001` is started, stopped or called (the image guard refuses a `:8001` base URL by itself).
   Checkable: `lsof -nP -iTCP:8040 -sTCP:LISTEN` shows the trial uvicorn, and `:8000`/`:8001` appear in no command of the wrapper.
4. **Caps and stops.** Cap 8.00 / warn 6.00 USD (item 1). Early stop (d): the first 10 `comment_attachment` events
   all `expired` and no `covered=True` line → `kill $RUNNER` (SIGTERM). Image rule, 5 minutes: the existing guard
   `docs/evidence/2026-10-28/scripts/paid-image-5min.py` runs beside the runner (wrapper above); within 300 s of the
   session's first frame it needs one `model_call` with purpose `card_cutout` and `ok` true and one card version
   whose `image_url` is a `data:image/` URL. It prints `IMAGE_OK` (card id, version, image sha256) and exits 0, or
   prints `IMAGE_MISSING`, SIGTERMs the runner and its children, prints `/api/cost?session=<run>` and exits 1; the
   NOTE then records the stop and that cost. The guard's log is `<run>-image-5min.log`, kept beside the NOTE.
5. **Binding and key.** The wrapper generates the two tokens (`secrets.token_urlsafe(32)`) and exports all four
   `C5_BIND_*` variables; its log shows only `binding: session <run> epoch epoch-<run> (two tokens generated, not
   shown)`. Runner flags: `--paid`, no `--observations` (the runner rejects the pair). `C5_OFFLINE` is unset by the
   wrapper and never set for a paid loop (`measure/c5_live.py:397`); the worker child would refuse it with a key
   (`measure/c5_proc.py:124`). `MODEL_API_KEY` is only tested for presence: the wrapper prints `set: yes|no`, the
   worker proof line prints `model_key_set: true`; no command prints the value. Checkable after the run:
   `grep -c 'model_key_set": true' <run>-runner.log` ≥ 1 and `grep -c offline <run>-loop1-worker.log` = 0.
6. **Log split.** Section (c): per expired comment, `missing read` vs `timing` (plus `unresolved`, `evidence rejected`,
   `ready`, `ambiguous`), needing the `--log-config` so the app's `attachment evaluation` lines exist. Frames the
   worker read: the last `C5PROC … "progress"` line of `<run>-runner.log` (`stats.vision`: `received`, `done`,
   `skipped`, `stale`, `failed`) and the same counts in the run file; the NOTE reports `done / 351` and the three
   non-read counts beside the two expiry numbers.
7. **Name, time, duration.** Run id `rescore-paid-1030-HHMM`, `HHMM` = the start minute in VN time, as named in the
   go line. Start: at the go line's time, on 30 October, by the branch owner from this worktree ((f)). Two
   stages ((e)): stage 1 `--seconds 120` ≈ 3 min wall, stage 2 `--seconds 350` only on its condition. Stage 2:
   create/migrate and app start ≈ 20 s, runner pre-flight ≈ 5 s, one loop 350 s of frames at speed 1 plus the
   runner's tail (`TAIL_MS`) and the loop-end barrier ≈ 6.5 min, teardown ≈ 10 s: about 7.5 minutes wall; the
   image guard decides at ≈ 5 min, the early-stop rule (d) at ≈ 2 min.

## What is still not decided here

- The go itself: the line that names stage 1's run id and stage 2's condition; nothing paid runs before it.
- The billing page screenshot after the run (Founder), against which the ledger's USD sum is set.

## Done on 29 October, 0 USD (this worktree)

Ledger database, created and migrated (`TEST_DATABASE_URL` = the `.env` URL with the name `keviq_paid_test`;
the script prints the name only):

```
TEST_DATABASE_URL name: /keviq_paid_test
migrate rc=0
created the test database
migrations applied: 001_events.sql, 002_attachments.sql, 003_attachment_inputs.sql
```

Databases on the compose server afterwards: `keviq`, `keviq_paid_test`, `keviq_test`; 11 tables in the new one
(`attachment_binding_history`, `attachment_bindings`, `attachment_consumed_inputs`, `attachment_order_intents`,
`attachment_source_receipts`, `attachments`, `cards`, `comments`, `events`, `orders`, `schema_migrations`);
`model_call` rows 0.

`dry-logconfig-2317`: `uvicorn measure.c5_bind:app` on 8040 with `--log-config` and `MODEL_API_KEY=""`,
healthz only, then stopped:

```
MODEL_API_KEY set: no
binding: session dry-logconfig-2317 epoch epoch-1793290626 (two tokens generated, not shown)
healthz: {"ok":true,"store":"pg","head_seq":0}
cost: {"today_usd":0.0,"total_usd":0.0,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.0,"session_cap_usd":8.0,"session_warn_usd":6.0}
state attachment_enabled: True
2026-10-29 23:17:07,365 uvicorn.error INFO Started server process [44572]
2026-10-29 23:17:07,515 uvicorn.error INFO Application startup complete.
2026-10-29 23:17:07,647 uvicorn.access INFO 127.0.0.1:50268 - "GET /healthz HTTP/1.1" 200
INFO lines from app-named loggers: 0
```

The log config works (uvicorn's own INFO lines carry the `%(name)s` format), but a healthz-only app has no
`app`-side INFO line to write: the only INFO site is the readiness handler. So the same config was run under
a 20 s unpaid bound loop, `dry-bound-2319` (the wrapper of (b) with `C5_OFFLINE=1`, a blank key and
`--seconds 20`, otherwise identical: ledger `keviq_paid_test`, `--log-config`, binding tokens generated):

```
DATABASE_URL name: /keviq_paid_test
MODEL_API_KEY set: no
binding: session dry-bound-2319 epoch epoch-dry-bound-2319 (two tokens generated, not shown)
migrations applied: none (up to date)
healthz: {"ok":true,"store":"pg","head_seq":0}
cost before: {"today_usd":0.0,"total_usd":0.0,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.0,"session_cap_usd":8.0,"session_warn_usd":6.0}
runner rc=0
session cap 8.0 USD, warning 6.0 USD (env)
loop 1: t0 1793290749586
loop 1: done, SETTLED/SETTLED behind 0, retired 1, waits {}, failures 0
run dry-bound-2319 sha bcdec6e271a9653e1cf3d19b2684cfc07f18dee6 loops 1
SELECT session_id, count(*) AS n, COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd
FROM events WHERE type = 'model_call' AND session_id = 'dry-bound-2319' GROUP BY session_id;
session_id | n | usd
(0 rows)
cost after: {"today_usd":0.0,"total_usd":0.0,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.0,"session_cap_usd":8.0,"session_warn_usd":6.0}
state after: {'attachment_enabled': True}
app log: 173 lines; attachments INFO lines: 5; uvicorn.access lines: 160
2026-10-29 23:19:14,162 attachments INFO attachment evaluation: covered=True resolved=False remaining_ms=2894
2026-10-29 23:19:14,838 attachments INFO attachment evaluation: covered=True resolved=False remaining_ms=2218
2026-10-29 23:19:15,820 attachments INFO attachment evaluation: covered=True resolved=False remaining_ms=1236
2026-10-29 23:19:16,552 attachments INFO attachment evaluation: covered=True resolved=False remaining_ms=504
2026-10-29 23:19:16,828 attachments INFO attachment evaluation: covered=True resolved=False remaining_ms=228
```

So the INFO lines of (c) exist under this config. Events of the session in the ledger: 36 (frame 20, speech 4,
comment_labeled 4, card 3, comment 3, comment_attachment 1, product_seen 1). The one `comment_attachment`
(`c5-1-001`, the only order comment whose deadline fell inside 20 s): `outcome` `expired`, `reason`
`original attachment deadline expired`, `candidates` `[]`, `selected_card_id` null, deadline 4000 ms after
`comment_ingested_ms`, five evaluations before it all `covered=True resolved=False` — class `unresolved` of
(c) (evidence accepted, nothing to resolve to: no vision read without a key), not `missing read`.

Two more rows are in `keviq_paid_test`, sessions `pytest-ver-4472206928` (card) and `pytest-pg` (comment),
seq 37 and 40, after the session's 36: the runner's code checks (`measure/c5.py` `code_checks()`, one pytest
per situation file, key blanked and `DATABASE_URL` removed, so `tests/conftest.py` took `TEST_DATABASE_URL`,
the ledger) wrote them; 16 situations checked, all rc 0 in the run file. They cost nothing and are
excluded from the paid session by id; they are left in place. The paid run will add the same kind of rows
after its loop.

Wrapper file: `rescore-paid-run.sh`, 4043 bytes, sha256 `31f7dee5d31ef812d2cf0c117238fb307317ed6f13fc27d204a37ff2ee03c053`, `bash -n` ok, not
run; its text is the block in (b). It contains no key, token, password or URL with credentials (the two
`secrets.token_urlsafe(32)` lines generate the binding tokens at run time).
