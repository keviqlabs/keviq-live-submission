#!/usr/bin/env bash
# L3 integration replay: real PostgreSQL, app and worker processes, the order cases from data/comments and demo1
# without fixtures, all over HTTP (measure/scenarios.py has the cases).
# Usage: make integration   (needs Docker; leaves the postgres container running, stops the app and worker it started)
# Runs on the test database (<name>_test, D-016) with the Model API key blanked: no project rows, no billed calls.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${SMOKE_PORT:-8778}"
BASE="http://127.0.0.1:${PORT}"
PREFIX="smoke-integ-$(date +%s)"
LOG_DIR="$(mktemp -d)"
PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
fail() {
  echo "INTEGRATION FAIL: $*" >&2
  echo "--- app log" >&2; tail -20 "$LOG_DIR/app.log" >&2 || true
  echo "--- worker log" >&2; tail -20 "$LOG_DIR/worker.log" >&2 || true
  exit 1
}

echo "1/4 postgres + test database"
DATABASE_URL="$(uv run python -c 'from app.settings import testing_database_url as t; print(t() or "")')"
[ -n "$DATABASE_URL" ] || fail "DATABASE_URL is not set (run through make integration)"
export DATABASE_URL
export MODEL_API_KEY=""  # set but empty, so .env cannot fill it in for the app or the worker
docker compose -f deploy/compose.yml up -d --wait postgres >"$LOG_DIR/infra.log" 2>&1 \
  || { cat "$LOG_DIR/infra.log" >&2; fail "postgres"; }
uv run python -m app.migrate --test >>"$LOG_DIR/infra.log" 2>&1 || { cat "$LOG_DIR/infra.log" >&2; fail "migrate"; }

echo "2/4 app on :$PORT"
uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$LOG_DIR/app.log" 2>&1 &
PIDS+=($!)
for _ in $(seq 1 50); do curl -sf "$BASE/healthz" >/dev/null 2>&1 && break; sleep 0.2; done
health="$(curl -sf "$BASE/healthz")" || fail "healthz did not answer"
echo "    healthz: $health"
echo "$health" | grep -q '"store":"pg"' || fail "app is not on PostgreSQL"

echo "3/4 worker"
uv run python -m worker --base-url "$BASE" >"$LOG_DIR/worker.log" 2>&1 &
WORKER=$!
PIDS+=($WORKER)
sleep 2
kill -0 "$WORKER" 2>/dev/null || { cat "$LOG_DIR/worker.log" >&2; fail "worker exited"; }

echo "4/4 scenarios -> $PREFIX-*"
rc=0
uv run python -m measure.scenarios --base-url "$BASE" --prefix "$PREFIX" --json "$LOG_DIR/scenarios.json" || rc=$?
kill -0 "$WORKER" 2>/dev/null || fail "worker exited during the scenarios"
[ "$rc" -eq 0 ] || fail "scenario checks failed (rc=$rc)"
echo "INTEGRATION OK ($PREFIX)"
