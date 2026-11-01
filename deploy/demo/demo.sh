#!/usr/bin/env bash
# Hosted demo control. Settings come from deploy/demo/.env (copy demo.env.example); runbook: deploy/demo/README.md.
#
#   deploy/demo/demo.sh up              build, run the start guard, start postgres + app + worker
#   deploy/demo/demo.sh seed            replay each of DEMO_SESSIONS into DEMO_SLOTS sessions <session>-judge<N>
#   deploy/demo/demo.sh reset           wipe all demo data (database volume included), then up + seed
#   deploy/demo/demo.sh replay SESSION TARGET   replay SESSION into session TARGET at speed 1, to watch it arrive
#   deploy/demo/demo.sh status          containers and the app health probe
#   deploy/demo/demo.sh logs [SERVICE]  follow the logs
#   deploy/demo/demo.sh down            stop the stack, keep the data
#   deploy/demo/demo.sh uninstall       stop the stack, delete its data volume and its image
#
# A shell variable overrides the same name in .env (as for docker compose). The script never sources .env, so a
# credential set in the shell still reaches the start guard and stops the stack.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${DEMO_ENV_FILE:-$HERE/.env}"
PROJECT=keviq-demo

if [ ! -f "$ENV_FILE" ]; then
  echo "demo: $ENV_FILE is missing; copy $HERE/demo.env.example to it and fill it in" >&2
  exit 2
fi

# DEMO_COMPOSE_OVERRIDE: an extra compose file, e.g. explicit subnets when Docker's default address pools are used up
compose() {
  docker compose -p "$PROJECT" -f "$HERE/compose.yml" ${DEMO_COMPOSE_OVERRIDE:+-f "$DEMO_COMPOSE_OVERRIDE"} \
    --env-file "$ENV_FILE" "$@"
}

# setting NAME DEFAULT: the shell value, else the last NAME= line in .env, else DEFAULT
setting() {
  local value="${!1:-}"
  if [ -z "$value" ]; then
    value="$(sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1)"
  fi
  value="${value%\"}"
  printf '%s' "${value#\"}" | grep . || printf '%s' "$2"
}

up() {
  compose build
  # The same entrypoint and environment as the services, minus the database: refuses when a credential is set.
  if ! compose run --rm --no-deps -T app true; then
    echo "demo: the start check failed (the guard refused, or see the error above); nothing was started" >&2
    exit 1
  fi
  compose up -d --wait --wait-timeout 180
}

replay_into() {  # replay_into SESSION TARGET SPEED
  compose exec -T app python -m connectors.sim.replay --session "$1" --target "$2" --speed "$3" \
    --base-url http://127.0.0.1:8080
}

seed() {
  local sessions slots speed
  sessions="$(setting DEMO_SESSIONS demo1)"
  slots="$(setting DEMO_SLOTS 5)"
  speed="$(setting DEMO_SEED_SPEED 20)"
  for session in $sessions; do
    for i in $(seq 1 "$slots"); do
      replay_into "$session" "${session}-judge${i}" "$speed"
    done
  done
}

cmd="${1:-}"
case "$cmd" in
  up) up ;;
  seed) seed ;;
  reset)
    compose down -v --remove-orphans
    up
    seed
    ;;
  replay)
    [ -n "${3:-}" ] || { echo "usage: demo.sh replay SESSION TARGET" >&2; exit 2; }
    replay_into "$2" "$3" 1
    ;;
  status)
    compose ps
    curl -fsS "http://127.0.0.1:$(setting DEMO_APP_PORT '')/healthz"
    echo
    ;;
  logs)
    shift
    compose logs --tail=200 -f "$@"
    ;;
  down) compose down ;;
  uninstall) compose down -v --rmi local --remove-orphans ;;
  *)
    sed -n '2,13p' "$0" >&2
    exit 2
    ;;
esac
