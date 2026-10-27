#!/usr/bin/env bash
# L3 smoke: real PostgreSQL, real app process, replay over HTTP, SSE read back, worker boots, measure from the DB.
# Usage: make smoke   (needs Docker; leaves the postgres container running, stops the app and worker it started)
# Runs on the test database (<name>_test, D-016) with the Model API key blanked: no project rows, no billed calls.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${SMOKE_PORT:-8765}"
BASE="http://127.0.0.1:${PORT}"
TARGET="smoke-$(date +%s)"
LOG_DIR="$(mktemp -d)"
PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
fail() { echo "SMOKE FAIL: $*" >&2; echo "--- app log" >&2; tail -20 "$LOG_DIR/app.log" >&2 || true; exit 1; }

echo "1/7 postgres + test database"
DATABASE_URL="$(uv run python -c 'from app.settings import testing_database_url as t; print(t() or "")')"
[ -n "$DATABASE_URL" ] || fail "DATABASE_URL is not set (run through make smoke)"
export DATABASE_URL
export MODEL_API_KEY=""  # set but empty, so .env cannot fill it in for the app or the worker
docker compose -f deploy/compose.yml up -d --wait postgres >"$LOG_DIR/infra.log" 2>&1 \
  || { cat "$LOG_DIR/infra.log" >&2; fail "postgres"; }
uv run python -m app.migrate --test >>"$LOG_DIR/infra.log" 2>&1 || { cat "$LOG_DIR/infra.log" >&2; fail "migrate"; }

echo "2/7 app on :$PORT"
uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$LOG_DIR/app.log" 2>&1 &
PIDS+=($!)
for _ in $(seq 1 50); do curl -sf "$BASE/healthz" >/dev/null 2>&1 && break; sleep 0.2; done
health="$(curl -sf "$BASE/healthz")" || fail "healthz did not answer"
echo "    healthz: $health"
echo "$health" | grep -q '"store":"pg"' || fail "app is not on PostgreSQL"

echo "3/7 worker boots and holds the stream"
uv run python -m worker --base-url "$BASE" >"$LOG_DIR/worker.log" 2>&1 &
WORKER=$!
PIDS+=($WORKER)
sleep 2
kill -0 "$WORKER" 2>/dev/null || { cat "$LOG_DIR/worker.log" >&2; fail "worker exited"; }

echo "4/7 replay demo1 -> $TARGET (speed 40)"
uv run python -m connectors.sim.replay --session demo1 --target "$TARGET" --speed 40 --base-url "$BASE" \
  >"$LOG_DIR/replay.log" 2>&1 || { cat "$LOG_DIR/replay.log" >&2; fail "replay"; }
tail -1 "$LOG_DIR/replay.log"

echo "5/7 state + SSE backlog"
curl -sf "$BASE/api/sessions/$TARGET/state" >"$LOG_DIR/state.json" || fail "state endpoint"
uv run python - "$LOG_DIR/state.json" <<'PY' || fail "state check"
import json, sys
s = json.load(open(sys.argv[1]))
queue = sorted(o["order_id"] for o in s["approval_queue"])
live = [c["card_id"] for c in s["live_cards"]]
print(f"    comments={len(s['comments'])} orders={len(s['orders'])} approval_queue={queue} live_cards={live}")
assert len(s["comments"]) == 14, "expected 14 comments"
assert queue == ["o-05", "o-14"], "expected o-05 and o-14 waiting for the seller"
assert "card-c" not in live, "card-c was retired by the quick switch"
PY
n_sse="$(curl -s --max-time 5 "$BASE/api/sessions/$TARGET/events?cursor=0&follow=false" | grep -c '^data:' || true)"
echo "    SSE backlog events: $n_sse"
[ "$n_sse" -eq 69 ] || fail "expected 69 events over SSE, got $n_sse"
kill -0 "$WORKER" 2>/dev/null || { cat "$LOG_DIR/worker.log" >&2; fail "worker died during replay"; }

echo "6/7 measure from PostgreSQL"
uv run python -m measure --session "$TARGET" --gold data/sessions/demo1/gold.json --no-write \
  | tee "$LOG_DIR/measure.txt"
grep -q "C1 consequence within 5000 ms: 15/15" "$LOG_DIR/measure.txt" || fail "C1 not 15/15"

echo "7/7 idempotent re-ingest"
seq_before="$(curl -sf "$BASE/healthz" | uv run python -c 'import json,sys; print(json.load(sys.stdin)["head_seq"])')"
uv run python -m connectors.sim.replay --session demo1 --target "$TARGET" --speed 1000 --base-url "$BASE" \
  >/dev/null 2>&1 || fail "second replay"
n_again="$(curl -s --max-time 5 "$BASE/api/sessions/$TARGET/events?cursor=0&follow=false" | grep -c '^data:' || true)"
[ "$n_again" -eq 69 ] || fail "duplicate events stored: $n_again"
echo "    head_seq before=$seq_before, events still $n_again"

echo "SMOKE OK ($TARGET)"
