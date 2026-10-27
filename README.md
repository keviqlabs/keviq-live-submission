# Keviq Live

Live-commerce assistant for small sellers who sell on Facebook livestreams: it turns what the seller shows and says on camera into honest product cards, reads the comment stream, drafts orders at the card price, and leaves every price change or discount to the seller.

## Built during the hackathon

Everything in this repository was written during the event (from 2026-10-26 23:24 ICT).

| Feature | Meta model | Commits | Measurement |
|---|---|---|---|
| Scaffold: event contracts, append-only store, SSE, replay on the session clock, cost-capped model client, measure | none called yet | branch `scaffold-contracts` | `make check` 58 tests; `make smoke` (real PostgreSQL) OK |
| Labeled comment stream and contact shield (`worker/brain`), 200 synthetic labeled comments | muse-spark-1.3, `reasoning.effort=minimal` | PR #3 | 200 comments at live speed: template-gold F1 0.976 (n=170), order fields 71/71, label p95 3.9 s (C8 target 1 s, not met) |
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

## Label latency (C8)

One real run on 27 October: 200 synthetic comments from `data/comments/` posted at their own pace (`connectors.sim.comments --speed 1`, about 9 minutes), muse-spark-1.3 with `reasoning.effort=minimal`, session `comments200-s1-1027-123307`. The target is label p95 ≤ 1 s and it is **not met**. Why and what was tried: `docs/DECISIONS.md` D-022. Evidence, both read from the stored events of the run: `docs/evidence/2026-10-27/measure-comments200-s1-1027-123307-124319.json` (`python -m measure`: the four stages, model_call) and `docs/evidence/2026-10-27/labels-comments200-s1-1027-123307.json` (`python -m measure.labels`: end to end, first hide, F1, order fields, cost). The command output is in PR #3.

| comment_labeled, ms | p50 | p95 | n |
|---|---|---|---|
| receive (comment stored → worker) | 7 | 13 | 233 |
| wait (worker → model call starts; the 1 s batch window) | 1016 | 1026 | 233 |
| infer (model call) | 1989 | 2879 | 200 |
| event delivery (worker emitted → app ingested; not console render, D-010) | 3 | 4 | 233 |
| **end to end, comment stored → model label stored** | **3007** | **3895** | 200 |
| model_call latency_ms | 1983 | 2958 | 199 |
| first hide, comment stored → hide label stored | 11 | 22 | 34 |

- 199 calls for 200 comments: at live arrival rates each batch holds one comment (one batch held two).
- receive, wait and event delivery include the 33 shield labels; infer and end to end are model labels only.
- 33 of the 34 hides come from the shield on receipt. The spelled-out phone number (c146) has no digits for the regex, so the model label hides it: at 3.7 s in this run, 4.4 s in `comments200-s1-1027-135456` and 3.9 s in `comments200-v2-1027-141048` (first hide). The evidence files give 4.10 s, 8.14 s and 5.42 s for these runs as `core.c5.phone_visible_max_s`, measured at the latest hide label as `measure/core.py` on main does; measuring C5 at the first hide needs PR #9.
- Spend: $0.2578 for 199 calls.
- Earlier run, for comparison: sim speed 20, default effort, batches of 6–9 comments, label p95 21.4 s.
- Two later runs with the same code and settings were slower: label p95 5.6 s and 5.3 s, model call p95 4.7 s and 4.3 s (`labels-comments200-s1-1027-135456.json`, and the next section). Session `comments200-s1-1027-135456` was made before two template rows were replaced by the cancel rows c169 and c192; its evidence file is kept as recorded and is not re-scored against the current gold.

## Labels against the approved gold

The Founder confirmed the review sample c001–c030 on 27 October (all 30, none changed). Five template rows were replaced by seller and cancel cases, and seven rows carry the action the shop should take next (`data/README.md`). One run on that set at live speed, same settings as above, session `comments200-v2-1027-141048`: 198 calls, $0.2589, 0 errors, written to the project database so the cost cap counts it. Evidence: `docs/evidence/2026-10-27/labels-comments200-v2-1027-141048.json` (`python -m measure.labels`) and `docs/evidence/2026-10-27/measure-comments200-v2-1027-141048-142057.json` (`python -m measure`, stages), command output in PR #7.

| | result |
|---|---|
| micro-F1, Founder-approved sample c001–c030 | 1.000, n=30 |
| micro-F1, template gold c031–c200 | 0.959, n=170 |
| order fields all exact | 73/74 |
| `ask_customer` (c019, c022, c106): no size or item guessed | 2/3 |
| `needs_seller` (c034 over the threshold, c092 discount): order filled in right | 2/2 |
| `confirm_cancel` (c192 "huỷ giúp em đôi size 39"): labeled `other` | 0/1 |
| `none` (c169 "đừng huỷ đơn em"): labeled `other` | 0/1 |
| scams with contact hidden | 26/26 |
| label end to end p50 / p95 | 3835 / 5325 ms |
| model_call latency_ms p50 / p95 | 2804 / 4331 ms |

- The model labels both cancel comments `complaint`. Contract v1 has no cancel label and the labeling prompt says nothing about cancelling, so the shop does not see the cancel as `other`. Not fixed here.
- c106 "size như bạn trên" was labeled `other`, so it got no order and no question. In the previous run on this row (session `comments200-s1-1027-135456`, before the cancel rows) it was an order with the item unresolved. The same comment gets different labels from run to run.
- These rows score the model's labels only. Whether the shop then asks the customer, waits for the seller or confirms a cancel is up to the orders and Messenger streams.

## Honest limits

- In `data/sessions/demo1` no model is called: the cards, labels and orders are hand-written stand-ins (`fx-` events), so the demo1 numbers check the measuring code, not the models. `uv run python -m connectors.sim.replay --session demo1 --no-fixtures` drops them.
- C8 label latency misses the 1 s target (table above). The last stage is event delivery, worker emitted → app ingested; console render time is not measured.
- The 200-comment labeled set is in `data/comments/` (how the gold is set: `data/README.md`); the Founder confirmed its review sample, c001–c030, on 27 October. It has one cancel row and one "don't cancel" row; the model labels both `complaint`. The 500-comment set is not built yet.
- Facebook is an interface only and Messenger is simulated: nothing is sent to real people and no real comment is hidden.
- The request and response shapes for the Model API (structured output, SAM, voice stream) come from the docs and are not yet checked against live calls.

## License

Apache-2.0 — see `LICENSE`.
