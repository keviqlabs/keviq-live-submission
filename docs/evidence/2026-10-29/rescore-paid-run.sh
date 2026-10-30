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
