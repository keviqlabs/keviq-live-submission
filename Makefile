# Local defaults match deploy/.env.example; a repo-root .env (gitignored) overrides them.
-include .env
export
DATABASE_URL ?= postgresql://keviq:keviq_local_dev@127.0.0.1:55433/keviq
APP_PORT ?= 8000
BASE_URL ?= http://127.0.0.1:$(APP_PORT)
SESSION ?= demo1
SPEED ?= 1
COMPOSE = docker compose -f deploy/compose.yml

.PHONY: infra-up infra-down app worker replay live-ingest lint fmt test ui-test measure-quick check smoke integration secrets hygiene hooks

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

live-ingest:
	uv run python -m connectors.facebook.ingest --session $(SESSION) --base-url $(BASE_URL)

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run python -m tests.db_lock -- pytest -q

ui-test:
	node --test tests/console_*_ui.mjs

measure-quick:
	uv run python -m measure --events-file data/sessions/demo1/events.jsonl --gold data/sessions/demo1/gold.json --no-write

check: lint hygiene ui-test test measure-quick

smoke:
	bash tests/smoke.sh

integration:
	bash tests/integration.sh

secrets:
	gitleaks detect --source . --no-banner --redact
	gitleaks detect --source . --no-banner --redact --no-git

hygiene:
	bash scripts/hygiene.sh

hooks:
	git config core.hooksPath .githooks

# Plays a recording into a session on the session clock: one frame a second, and the seller's speech when a model
# key is set (without one, speech is skipped). The recording stays on this machine; never commit it.
#   make media SESSION=<session id> VIDEO=<path to the recording> [MEDIA_ARGS="--seconds 60 --no-voice"]
.PHONY: media c5-comments
media:
	uv run python -m worker.media --session $(SESSION) $(if $(VIDEO),--video "$(VIDEO)") --base-url $(BASE_URL) --speed $(SPEED) $(MEDIA_ARGS)

# Posts C5 stream-<LOOP> (data/comments/c5) into the session on the recording's clock; start it with `make media`.
#   make c5-comments SESSION=<session id> [LOOP=1] [COMMENTS_ARGS="--seconds 60"]
c5-comments:
	uv run python scripts/c5_comments.py --session $(SESSION) --loop $(or $(LOOP),1) --base-url $(BASE_URL) $(COMMENTS_ARGS)

# Draws the judge1 video (four products, 64 s, no sound) into data/local/judge1/judge1.mp4; needs ffmpeg.
#   make judge-video
# Then, with `make app` and `make worker` running, play it and post judge1's comments and speech on its clock:
#   make media SESSION=<new session id> VIDEO=data/local/judge1/judge1.mp4 MEDIA_ARGS=--no-voice
#   make judge-feed SESSION=<the same session id>
.PHONY: judge-video judge-feed
judge-video:
	uv run python scripts/judge1.py video

judge-feed:
	uv run python scripts/judge1.py feed --session $(SESSION) --base-url $(BASE_URL) $(FEED_ARGS)
