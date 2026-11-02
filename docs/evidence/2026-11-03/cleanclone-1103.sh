#!/usr/bin/env bash
# Clean-clone timing for README mode 1 (replay judge1, no key): clone -> uv sync (cold cache) -> infra-up (own compose
# project on port 55499, so another database container on the machine is untouched) -> app -> replay judge1 -> console 200.
set -u
unset MODEL_API_KEY DATABASE_URL
OUT=<log>; : > "$OUT"
T(){ echo "$(date +%s) $*" >> "$OUT"; }
D=<clone>
P=keviq-cleanclone-1103
export UV_CACHE_DIR=<uv-cache> POSTGRES_PORT=55499
export DATABASE_URL=postgresql://keviq:keviq_local_dev@127.0.0.1:55499/keviq
rm -rf "$UV_CACHE_DIR" "$D"
COMPOSE="docker compose -p $P -f deploy/compose.yml"
echo "image cached: $(docker image inspect postgres:17-alpine --format '{{.Id}}' 2>/dev/null | cut -c1-19)" >>"$OUT"
T start
gh repo clone <repo> "$D" >>"$OUT" 2>&1; T "clone rc=$?"  # <repo>: the working repository, authenticated gh clone
cd "$D" || exit 1
git log -1 --format='head %H' >>"$OUT"
uv sync >>"$OUT" 2>&1; T "uv-sync rc=$?"
make infra-up COMPOSE="$COMPOSE" >>"$OUT" 2>&1; T "infra-up rc=$?"
make app APP_PORT=8072 ><app-log> 2>&1 &
for i in $(seq 1 120); do curl -sf http://127.0.0.1:8072/healthz >/dev/null 2>&1 && break; sleep 1; done
T "app-ready healthz=$(curl -s http://127.0.0.1:8072/healthz)"
make replay SESSION=judge1 APP_PORT=8072 >>"$OUT" 2>&1; T "replay rc=$?"
SID=$(grep -o 'console/judge1-[0-9-]*' "$OUT" | head -1 | cut -d/ -f2)
T "console sid=$SID http=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8072/console/$SID) healthz=$(curl -s http://127.0.0.1:8072/healthz)"
pkill -INT -f "uvicorn app.main:app --host 127.0.0.1 --port 8072" 2>/dev/null; sleep 2
$COMPOSE down -v >>"$OUT" 2>&1; T "teardown rc=$?"
