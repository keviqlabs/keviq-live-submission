# Keviq Live

Live-commerce assistant for small sellers who sell on Facebook livestreams: it turns what the seller shows and says on camera into honest product cards, reads the comment stream, drafts orders at the card price, and leaves every price change or discount to the seller.

## Built during the hackathon

Everything in this repository was written during the event (from 2026-10-26 23:24 ICT).

| Feature | Meta model | Commits | Measurement |
|---|---|---|---|
| Scaffold: event contracts, append-only store, SSE, replay on the session clock, cost-capped model client, measure | none called yet | branch `scaffold-contracts` | `make check` 58 tests; `make smoke` (real PostgreSQL) OK |
| _filled in daily_ | | | |

**Prior work.** Before the event the author built an earlier prototype (a replay console) in a separate private repository. None of its code is in this repository.

AI coding assistants were used as tools. The entrant is the Founder, working alone.

## Run it in 5 minutes

Needs Docker and [uv](https://docs.astral.sh/uv/) (uv fetches Python 3.12). No API key is needed for the replay.

```bash
uv sync
make infra-up                 # PostgreSQL 17 on 127.0.0.1:55433, migrations applied
make app                      # http://127.0.0.1:8000 — keep this terminal open
make replay SESSION=demo1     # second terminal; prints the console URL for the replayed session
make measure-quick            # C1 / C3 / C5 / C8 / cost table for demo1
```

Open the printed console URL to watch cards, comments, orders and the seller's approval queue fill in on the session clock. `make worker` starts the model worker (handlers are added by each stream); `make check` runs lint, tests and the quick measure; `make smoke` runs the whole path against real PostgreSQL.

For model calls, copy `deploy/.env.example` to `.env` at the repo root and set `MODEL_API_KEY` there. Calls stop at 30 USD a day or 300 USD in total, and each call is logged as a `model_call` event.

How the pieces fit: `docs/architecture.md`. Why: `docs/DECISIONS.md`.

## Honest limits

- In the scaffold no model is called yet. In `data/sessions/demo1`, the cards, labels and orders are hand-written stand-ins (`fx-` events), so the demo1 numbers check the measuring code, not the models. `uv run python -m connectors.sim.replay --session demo1 --no-fixtures` drops them.
- C8 latency has no data yet. Its display stage counts worker emit to app store; console render time is not measured.
- The labeled comment sets used for C3 and C5 (200 and 500 comments) are not built yet; demo1 has 14 comments.
- Facebook is an interface only and Messenger is simulated: nothing is sent to real people and no real comment is hidden.
- The request and response shapes for the Model API (structured output, SAM, voice stream) come from the docs and are not yet checked against live calls.

## License

Apache-2.0 — see `LICENSE`.
