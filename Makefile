# Local defaults match deploy/.env.example; a repo-root .env (gitignored) overrides them.
-include .env
export
DATABASE_URL ?= postgresql://keviq:keviq_local_dev@127.0.0.1:55433/keviq
APP_PORT ?= 8000
BASE_URL ?= http://127.0.0.1:$(APP_PORT)
SESSION ?= demo1
SPEED ?= 1
COMPOSE = docker compose -f deploy/compose.yml

.PHONY: infra-up infra-down app worker replay lint fmt test measure-quick check smoke integration secrets

infra-up:
	$(COMPOSE) up -d --wait postgres
	uv run python -m app.migrate

infra-down:
	$(COMPOSE) down

app:
	uv run uvicorn app.main:app --host 127.0.0.1 --port $(APP_PORT)

worker:
	uv run python -m worker --base-url $(BASE_URL)

replay:
	uv run python -m connectors.sim.replay --session $(SESSION) --speed $(SPEED) --base-url $(BASE_URL)

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run python -m tests.db_lock -- pytest -q

measure-quick:
	uv run python -m measure --events-file data/sessions/demo1/events.jsonl --gold data/sessions/demo1/gold.json --no-write

check: lint test measure-quick

smoke:
	bash tests/smoke.sh

integration:
	bash tests/integration.sh

secrets:
	gitleaks detect --source . --no-banner --redact
	gitleaks detect --source . --no-banner --redact --no-git
