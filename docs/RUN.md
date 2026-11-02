# Running Keviq Live, step by step

The README ("Run it yourself") has the short version. This file gives each step with the output to expect, how to run a second copy on other ports, and the paid run on the test1 recording. The outputs were copied from a run on a fresh clone; session ids, times and ports differ on your machine.

## 0. Clone and install

```bash
git clone https://github.com/keviqlabs/keviq-live-event.git
cd keviq-live-event
uv sync
```

`uv sync` creates `.venv` and lists the packages it installed (`+ fastapi==…`, `+ psycopg==…`, …); on a machine without Python 3.12 it downloads one first.

Modes 1 and 2 need no `.env`. Without one, the Makefile uses the database and app port of `deploy/.env.example`, and the model key is unset, so no model is called.

## 1. Database

```bash
make infra-up
```

On a new machine Docker first pulls the PostgreSQL image and prints the volume and container lines (`Volume … Created`, `Container … Started`). The output ends with:

```
docker compose -f deploy/compose.yml up -d --wait postgres
 Container keviq-live-postgres-1 Healthy
uv run python -m app.migrate
migrations applied: 001_events.sql, 002_attachments.sql, 003_attachment_inputs.sql
```

On a database that already has them: `migrations applied: none (up to date)`. `make infra-down` stops the container and keeps its data volume.

## 2. App

```bash
make app        # keep this terminal open
```

It prints `Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)`. From another terminal, `curl -s http://127.0.0.1:8000/healthz` prints `{"ok":true,"store":"pg","head_seq":0}` on a new database (`head_seq` counts the stored events).

## 3. Mode 1: replay judge1 (no key, 0 USD)

```bash
make replay SESSION=judge1
```

```
replay data/sessions/judge1/events.jsonl: 69 events -> judge1-1101-190129
console: http://127.0.0.1:8000/console/judge1-1101-190129
done: 69 events posted
```

The session id carries the start time. Open the console address as soon as it is printed; the replay takes about 65 s. The README's "What the judge1 replay shows" lists what happens, in order. When it is done the console shows six orders: `o-06` and `o-11` confirmed, each with a receipt; `o-07` and `o-08` waiting for the customer's answer; `o-05` (a discount) and `o-14` (2,070,000 VND) in the seller's queue; and two hidden comments.

Every card, label and order in judge1 is a hand-written stand-in, not model output. The numbers they give, read from the file:

```bash
uv run python -m measure --events-file data/sessions/judge1/events.jsonl --gold data/sessions/judge1/gold.json --no-write
```

```
events: 69  (hand-written fx- fixtures: 46; numbers over fixtures are not model results)
C1 consequence within 5000 ms: 15/15  missing=[]
C3 referenced_card_id accuracy: 1.000 on n=7 (target >= 0.9 on 50)
C5 order recall: 1.000 on n=7 (target >= 0.9)
C5 seller interventions: 0 in 1.03 min = 0.0/h (target <= 10/h)
C5 phone visible max: 1.5 s on n=1 (target <= 5 s)
C5 receipts with image and version price: 2/2 (target 100%)
labels micro-F1: 1.000 (14/14 labeled)
C8 latency ms (p50 / p95, n):
  no events with timing yet (fixtures carry none; worker output does)
cost by day and model:
  no model_call events
```

`make measure-quick` prints the same table for `data/sessions/demo1`, whose events judge1 copies. These numbers check the measuring code, not the models.

## 4. Mode 2: play the judge1 video (no key, 0 USD)

Needs ffmpeg and ffprobe. With `make app` running:

```bash
make worker                   # terminal 2; keep it open
```

It logs `worker: consuming /api/events from seq N (7 event types handled)`, where N is the number of events already stored (69 after one mode 1 replay): it handles only events posted after it starts, so it leaves the replay alone.

```bash
make judge-video
```

```
uv run python scripts/judge1.py video
data/local/judge1/judge1.mp4: 64 s, 720x1280, no sound
```

About 15 s. Then, in two terminals, the second right after the first:

```bash
make media SESSION=judge1-video VIDEO=data/local/judge1/judge1.mp4 MEDIA_ARGS=--no-voice
make judge-feed SESSION=judge1-video
```

`make media` logs one `POST …/api/sessions/judge1-video/events` a second and ends with `{'frames': 64, 'speech': None, 'seconds': 63.1}`. `make judge-feed` prints:

```
session judge1-video: 17 comments and speech lines of judge1, clock started 1.0 s ago
console: http://127.0.0.1:8000/console/judge1-video
done: 17 events posted
```

`make judge-feed` waits up to 120 s for the session's first frame (`FEED_ARGS="--wait 300"` waits longer) and starts its clock there, so give each play a new session id. The worker logs `MODEL_API_KEY is not set in the environment (.env)`: product reads are skipped and comments are labelled `other`. In the console of `judge1-video`: 64 frames, 14 comments with the phone-and-link comment hidden by the contact shield (a regular expression, no model), the 3 speech lines, and no card or order.

## 5. Mode 3: real models (paid)

```bash
cp deploy/.env.example .env     # then set MODEL_API_KEY in .env to your key; .env is gitignored, never commit it
```

The example file also sets the database address and the app port to the defaults; change them in `.env` if you use other ports (section 7). The caps are described in the README (mode 3). Stop `make app` and `make worker` (Ctrl+C) and start them again so they read `.env`. Then check the caps the app runs with:

```bash
curl -s "http://127.0.0.1:8000/api/cost?session=judge1-paid"
```

```
{"today_usd":0.0,"total_usd":0.0,"daily_cap_usd":30.0,"daily_warn_usd":20.0,"total_cap_usd":300.0,"test_sessions":{"prefixes":["pytest","smoke-"],"today_usd":0.0,"total_usd":0.0},"session_usd":0.0,"session_cap_usd":null,"session_warn_usd":null}
```

(`session_cap_usd` is `null` until `MODEL_SESSION_CAP_USD` is set.) Then play the video with a key:

```bash
make media SESSION=judge1-paid VIDEO=data/local/judge1/judge1.mp4 MEDIA_ARGS=--no-voice
make judge-feed SESSION=judge1-paid
```

This was run with a key on the judge1 video in four sessions on 2 November 2026; two are written up. Session `judge-paid-1102-1109` on main `aa457d9`: 51 model calls, 0.0879 USD; 4 cards; of the 7 comments meant to become orders, 2 did as expected, 3 went to a wrong card and 2 made no order; no receipt was produced. Session `judge-paid-1102-1644` on main `46a0f0e` (after decision D-038 and the colour check of PR #100): 52 model calls, 0.0747 USD; 3 cards; of the same 7 comments, 3 did as expected, 1 went to the right card in another state, 2 went to a wrong card and 1 made no order; no receipt was produced. Both written-up results are FAIL (`docs/evidence/2026-11-02/judge1-demo1-paid-1102-NOTE.md`). The same `curl` shows the spend afterwards.

## 6. The paid run on the test1 recording

The measured paid run (`docs/evidence/2026-10-31/rescore-paid-1031-NOTE.md`) played the test1 recording, which is not in this repository. To run it you need, at these paths:

- `data/local/test1/vi-2026-10-27.mp4`: the recording, 5 min 51 s with sound;
- `data/local/test1/labels.json`: its local price labels, which the scorer reads.

Where they can be downloaded and their checksums: `data/judge/README.md`.

To watch it without scoring: in `.env` set `CATALOG_PATH=data/comments/c5/catalog.json` (the ten items of the recording) and the key, restart `make app` and `make worker`, then, the second right after the first:

```bash
make media SESSION=test1        # plays the first video in data/local/test1/ with its sound (speech to text)
make c5-comments SESSION=test1 LOOP=1
```

To score it as the measured run did: in `.env` also set `MODEL_SESSION_CAP_USD=8` and `MODEL_SESSION_WARN_USD=6` (the runner checks that the app reports the cap it runs with), start `make app` again, stop `make worker` (the runner starts its own worker and media for each loop), create the test database once with `uv run python -m app.migrate --test`, then:

```bash
uv run python -m measure.c5 --base-url http://127.0.0.1:8000 --loops 1 --seconds 350 --paid
```

It writes `<run>.json`, `<run>-scenarios.json` and `<run>-comments.jsonl` to its `--out` folder and prints the `model_call` ledger of the session. At the end it runs each situation's test files with pytest on the test database. The measured run used the wrapper `docs/evidence/2026-10-29/rescore-paid-run.sh`, which also binds the run's events to generated tokens (`measure/c5_bind.py`) and watches for the card picture; its exact output is in the note. Neither command was run from a fresh clone for this guide.

### The full-size paid run (5 loops, 500 comments)

The full-size run on 2 November (`c5-paid-1102-1028`, `docs/evidence/2026-11-02/c5-paid-1102-1028-NOTE.md`) used the wrapper `scripts/c5_paid_run.sh`. It plays all five streams of `data/comments/c5/` over five loops of the test1 recording (about 33 minutes) with `CATALOG_PATH=data/comments/c5/catalog.json` and a session cap of 8 USD (warn 6). The scripts are stored without the executable bit, so call them with `bash`, from the repository root:

```bash
MAIN_ENV=<path of the .env holding MODEL_API_KEY> OTHER_TODAY_USD=<spend today not in the database> OTHER_TOTAL_USD=<total spend not in the database> \
  bash scripts/c5_paid_run.sh c5-paid-MMDD-HHMM <out folder> <database>_test [port]
```

- The run id starts with `c5-paid-` and is new for every attempt; the wrapper refuses an id whose `<run>.json` already exists in the out folder.
- The database name ends in `_test` and is never `keviq_test` (the one `make check` uses); it is created and migrated if missing. The port defaults to 8040.
- The wrapper sets the app's day and total caps from `OTHER_TODAY_USD` and `OTHER_TOTAL_USD` and refuses to start with less than 8 USD left under them. `PREFLIGHT_ONLY=1` stops after the database, migration, app and caps checks, with no model call. `PR81_HEAD` is optional (see the comment at the top of the script).
- It needs the test1 files listed above and refuses to run when tracked files differ from `HEAD`.
- Stop it with Ctrl-C or `kill -TERM <wrapper pid>`; its trap stops the runner, the per-loop children and the app.

In a second terminal, the watcher starts nothing and sends SIGTERM to the wrapper on its stop rules (expired attachments, 401/422 on the system attachment route, failed seller decisions, spend at the warn line or over 0.25 USD per minute):

```bash
bash scripts/c5_paid_watch.sh c5-paid-MMDD-HHMM <out folder> <database>_test [port] [postgres container]
```

These commands are copied from the scripts' own usage lines; they were not run from a fresh clone for this guide.

## 7. Other ports

To run a second copy beside one that uses 8000 and 55433, give it its own compose project, database port and app port. In every terminal of that copy:

```bash
export COMPOSE_PROJECT_NAME=keviq-judge POSTGRES_PORT=55471 APP_PORT=8070
export DATABASE_URL=postgresql://keviq:keviq_local_dev@127.0.0.1:55471/keviq
make infra-up
```

`BASE_URL` follows `APP_PORT`, so `make app`, `make worker`, `make replay`, `make media` and `make judge-feed` then use port 8070. To use another database name on that server, create it before the migrations:

```bash
docker compose -f deploy/compose.yml up -d --wait postgres
docker compose -f deploy/compose.yml exec -T postgres createdb -U keviq keviq_judge
export DATABASE_URL=postgresql://keviq:keviq_local_dev@127.0.0.1:55471/keviq_judge
make infra-up
```

With a `.env`, put the same names in it instead: for `make`, a value in `.env` wins over the shell's. `docker compose -f deploy/compose.yml down -v` (with the same `COMPOSE_PROJECT_NAME`) removes that copy's container and its data.
