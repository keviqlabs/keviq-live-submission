#!/bin/bash
# Full-size paid C5 run (5 loops, 500 comments, about 33 min): scripts/c5_paid_run.sh <run> <out> <db> [port]
#   <run>  c5-paid-MMDD-HHMM, new for every attempt (the binding epoch and both tokens are scoped to it)
#   <db>   database name ending in _test, created and migrated if missing; never keviq_test (pytest's database)
# Environment (read here, never printed):
#   MAIN_ENV          path of the main checkout .env holding MODEL_API_KEY (same as rescore-paid-run.sh)
#   OTHER_TODAY_USD   model spend today that is NOT in <db> (the app's caps only sum <db>)
#   OTHER_TOTAL_USD   model spend in total that is NOT in <db>
#   PR81_HEAD         optional: head commit of PR #81, when the tree merged it without a "Merge pull request #81" subject
#   PREFLIGHT_ONLY=1  stop after the cost pre-flight: database, migration, app and caps checked, no model call made
# Stop: Ctrl-C or `kill -TERM <this script's pid>`; the trap stops the runner, its per-loop children and the app.
set -uo pipefail
unset MODEL_DAILY_CAP_USD MODEL_DAILY_WARN_USD MODEL_TOTAL_CAP_USD   # only the values computed below may reach the app
RUN=$1; OUT=$2; DB=$3; PORT=${4:-8040}
BASE=http://127.0.0.1:$PORT
[ -f measure/c5_bind.py ] || { echo "run from the repo root"; exit 1; }
case "$RUN" in c5-paid-*) ;; *) echo "run id must start with c5-paid-"; exit 1;; esac
case "$DB" in keviq_test) echo "refusing keviq_test"; exit 1;; *_test) ;; *) echo "database name must end in _test"; exit 1;; esac
: "${MAIN_ENV:?}" "${OTHER_TODAY_USD:?}" "${OTHER_TOTAL_USD:?}"
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "tracked files differ from HEAD; refusing"; exit 1; }
[ -s data/local/test1/labels.json ] || { echo "data/local/test1/labels.json is missing (local gold the shop prices from); refusing"; exit 1; }
mkdir -p "$OUT"; [ -e "$OUT/$RUN.json" ] && { echo "$OUT/$RUN.json exists; use a new run id"; exit 1; }
cp docs/evidence/2026-10-29/rescore-paid-log-config.json "$OUT/log-config.json"

# 1. What this run runs on, written before anything starts.
NOTE="$OUT/$RUN-NOTE.txt"
PRS=$(git log --first-parent --merges --format=%s HEAD | sed -nE 's/^Merge pull request #([0-9]+)[: ].*/\1/p' | tr '\n' ' ')
{ echo "run: $RUN"; echo "start_utc: $(date -u +%FT%TZ)"; echo "head: $(git rev-parse HEAD)"
  echo "branch: $(git rev-parse --abbrev-ref HEAD)"; echo "origin_main: $(git rev-parse origin/main 2>/dev/null)"
  echo "dirty: no"; echo "merged_prs: $PRS"; echo "database: $DB"; echo "port: $PORT"; } > "$NOTE"
if ! echo " $PRS " | grep -q ' 81 '; then
  if [ -n "${PR81_HEAD:-}" ] && git merge-base --is-ancestor "$PR81_HEAD" HEAD; then echo "pr81_head_in_tree: $(git rev-parse "$PR81_HEAD")" >> "$NOTE"
  else echo "PR #81 is not in this tree; refusing (see $NOTE)"; exit 1; fi
fi
cat "$NOTE"

# 2. Database, key, caps (the caps only see <db>, so they are lowered by the spend recorded elsewhere).
ENVF=.env; [ -f "$ENVF" ] || ENVF="$MAIN_ENV"   # a fresh worktree has no .env: take the server from MAIN_ENV
DBURL="$(sed -n 's/^DATABASE_URL=//p' "$ENVF" | tr -d '"' | tr -d "'")"
[ -n "$DBURL" ] || { echo "no DATABASE_URL in $ENVF; refusing"; exit 1; }
export DATABASE_URL="${DBURL%/*}/$DB" TEST_DATABASE_URL="${DBURL%/*}/$DB"
echo "database name: /${DATABASE_URL##*/}"
export MODEL_API_KEY="$(sed -n 's/^MODEL_API_KEY=//p' "$MAIN_ENV" | tr -d '"' | tr -d "'")"
[ -n "$MODEL_API_KEY" ] || { echo "no model key; refusing"; exit 1; }
CAPS=$(python3 -c 'import sys; d, t = (float(x) for x in sys.argv[1:3]); print(f"{30 - d:.2f} {max(20 - d, 0):.2f} {300 - t:.2f}")' \
  "$OTHER_TODAY_USD" "$OTHER_TOTAL_USD") || { echo "OTHER_TODAY_USD and OTHER_TOTAL_USD must be numbers; refusing"; exit 1; }
read -r CAP_D WARN_D CAP_T <<< "$CAPS"
if ! python3 -c 'import sys; sys.exit(0 if min(float(sys.argv[1]), float(sys.argv[2])) >= 8 else 1)' "$CAP_D" "$CAP_T"; then
  echo "less than 8 USD left under the caps (day $CAP_D, total $CAP_T); refusing"; exit 1
fi
export MODEL_DAILY_CAP_USD=$CAP_D MODEL_DAILY_WARN_USD=$WARN_D MODEL_TOTAL_CAP_USD=$CAP_T
export MODEL_SESSION_CAP_USD=8 MODEL_SESSION_WARN_USD=6 CATALOG_PATH=data/comments/c5/catalog.json
unset C5_OFFLINE
export C5_BIND_SESSION="$RUN" C5_BIND_EPOCH="epoch-$RUN"
C5_BIND_SOURCE_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
C5_BIND_SYSTEM_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
export C5_BIND_SOURCE_TOKEN C5_BIND_SYSTEM_TOKEN
echo "caps: day $MODEL_DAILY_CAP_USD total $MODEL_TOTAL_CAP_USD session 8 (warn 6); two tokens generated, not shown"

# 3. App, with a bounded graceful shutdown (an open SSE client otherwise holds it forever).
uv run python -m app.migrate --test || exit 1
if curl -s --max-time 1 "$BASE/healthz" >/dev/null 2>&1; then echo "port $PORT already answers; refusing"; exit 1; fi
uv run uvicorn measure.c5_bind:app --host 127.0.0.1 --port "$PORT" --timeout-graceful-shutdown 10 \
  --log-config "$OUT/log-config.json" > "$OUT/$RUN-app.log" 2>&1 &
APP=$!; RUNNER=""; GUARD=""
stop() {
  echo "$(date +%T) stopping"
  [ -n "$RUNNER" ] && kill -TERM "$RUNNER" 2>/dev/null
  pkill -TERM -f "measure.c5 --base-url $BASE" 2>/dev/null
  pkill -TERM -f "measure.c5_proc .*$RUN" 2>/dev/null
  [ -n "$GUARD" ] && kill "$GUARD" 2>/dev/null
  sleep 3; pkill -KILL -f "measure.c5_proc .*$RUN" 2>/dev/null
  kill "$APP" 2>/dev/null; wait "$APP" 2>/dev/null; echo "app stopped"
}
trap 'stop; exit 130' INT TERM
for _ in $(seq 1 60); do curl -s --max-time 1 "$BASE/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
COST_BEFORE="$(curl -s --max-time 2 "$BASE/api/cost?session=$RUN")"
if ! python3 - "$COST_BEFORE" "$MODEL_DAILY_CAP_USD" "$MODEL_TOTAL_CAP_USD" <<'PY'
import json, sys
d, cap_d, cap_t = json.loads(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
got = (d.get("session_usd"), d.get("session_cap_usd"), d.get("session_warn_usd"), d.get("daily_cap_usd"), d.get("total_cap_usd"))
ok = got == (0.0, 8.0, 6.0, cap_d, cap_t)
print("cost pre-flight", "ok" if ok else f"WRONG {got}")
sys.exit(0 if ok else 1)
PY
then stop; echo "nothing run"; exit 1; fi
if [ "${PREFLIGHT_ONLY:-}" = 1 ]; then trap - INT TERM; stop; echo "pre-flight only: nothing run"; exit 0; fi

# 4. The run: 5 loops, no --seconds, host kept awake.
START=$(date +%s)
caffeinate -i -s uv run python -m measure.c5 --base-url "$BASE" --loops 5 --paid --run "$RUN" --out "$OUT" \
  > "$OUT/$RUN-runner.log" 2>&1 &
RUNNER=$!
uv run python docs/evidence/2026-10-28/scripts/paid-image-5min.py --base-url "$BASE" --run "$RUN" --window-s 300 \
  > "$OUT/$RUN-image-guard.log" 2>&1 &
GUARD=$!
wait $RUNNER; rc=$?
echo "runner rc=$rc wall_s=$(( $(date +%s) - START ))" | tee -a "$NOTE"
if kill -0 $GUARD 2>/dev/null; then kill $GUARD 2>/dev/null; echo "guard: no IMAGE_OK within 300 s"; else wait $GUARD; echo "guard rc=$?"; fi
grep -E 'IMAGE_OK|IMAGE_MISSING' "$OUT/$RUN-image-guard.log" | head -3
echo "cost after: $(curl -s --max-time 2 "$BASE/api/cost?session=$RUN")" | tee -a "$NOTE"
trap - INT TERM; stop
exit $rc
