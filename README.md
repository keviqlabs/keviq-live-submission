# Keviq Live

Live-commerce assistant for small sellers who sell on Facebook livestreams: it turns what the seller shows and says on camera into honest product cards, reads the comment stream, drafts orders at the card price, and leaves every price change or discount to the seller.

## Built during the hackathon

Everything in this repository was made during the event, from 26 October 2026 23:24 ICT; what existed before was two earlier private repositories, planning notes, the Meta Model API account and the shop's Facebook Page, and nothing from those repositories is in this one.

| Feature | Meta model | Commits | Measurement |
|---|---|---|---|
| Scaffold: event contracts, append-only store, SSE, replay on the session clock, cost-capped model client, measure | none called yet | branch `scaffold-contracts` | `make check` 58 tests; `make smoke` (real PostgreSQL) OK |
| Labeled comment stream and contact shield (`worker/brain`), 200 synthetic labeled comments | muse-spark-1.3, `reasoning.effort=minimal` | PR #3 | 200 comments at live speed: template-gold F1 0.976 (n=170), order fields 71/71, label p95 3.9 s (C8 target 1 s, not met) |
| Product read from the camera (`worker/vision/spark.py` → `product_seen`), prompt 6b | muse-spark-1.3, `reasoning.effort=minimal` | PR #15, #18, #20 | 351 frames of one test video (test1, 720×1280), one run: category right 299/351 (electronics 82/100, shoes 109/129, tops 79/93, no product 29/29), product change seen within 3 s 6/11, model_call p50/p95 1872/3168 ms, $0.8226. The prompt was tuned on 20 frames of the same video (limits below) |
| _filled in daily_ | | | |

**Before the event** (before 26 October 23:24 ICT; none of it is in this repository):
- Two private repositories by the author: the earlier product repository, with a prototype replay console (worked on until the week of 25 October), and a pre-event baseline repository (one commit, pushed 26 October 20:01 ICT). No code, data or images from either were copied, retyped or consulted here.
- Planning notes written on the afternoon of 26 October: a plan that separates existing work from event work, and a baseline task note. They are documents, not code.
- Accounts and know-how: the Meta Model API account and its credit, the shop's Facebook Page, and the author's experience of selling on livestreams and of the order flow.

**During the event** (from 26 October 23:24 ICT):
- All code, contracts, tests, data and docs in this repository. The first commit is dated 27 October 10:22 ICT, and no commit is older than the event start: `git log --format=%aI%n%cI | sort | head -1`.
- Outside this repository: the test1 recording (27 October, kept local and out of Git), the project brief with criteria C1–C11 and the day-1 prompt (27 October morning), and a private UI mock-up (27 October) that is kept apart; nothing from the mock-up was copied here.

AI coding assistants were used as tools. The entrant is the Founder, working alone. Some branch names carry an assistant's default prefix: `claude/` on 14 merged PRs between #2 and #29, and `codex/` on PR #47 and on draft PRs #48–#50, which were closed unmerged and reopened on tool-free branches as #53–#55. Those prefixes are the default branch names of the coding tools used during the hackathon. The current convention: a pull request is merged under its written title, and commits carry the author's private GitHub address.

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

## Architecture

The components (connectors, event store, workers, console) and the events between them are described in `docs/architecture.md`. The reasons behind the design are recorded in `docs/DECISIONS.md`.

## Level 2 (real Page, read-only)

`connectors/facebook` reads the comments of one live video on a Page through the Graph API and appends them to an app session as `comment` events with `source: facebook`, through the same `POST /api/sessions/{id}/events` the simulator uses; the app stays the only writer. The connector only issues GET requests (a transport guard refuses any other method before it reaches the network), sends no message, hides no comment, and keeps the Page token in the Authorization header, out of URLs, logs and error messages. Commenter ids are hashed into `user_ref`; display names are never requested.

Set in `.env` (fake values in `deploy/.env.example`): `FACEBOOK_PAGE_TOKEN`, `FACEBOOK_LIVE_VIDEO_ID`, and optionally `FACEBOOK_GRAPH_VERSION` (default `v26.0`) and `FACEBOOK_POLL_SECONDS` (default `2`). Then, with `make app` running:

```bash
make live-ingest SESSION=<session id>                                  # polls until stopped
uv run python -m connectors.facebook.ingest --session <session id> --once   # one poll, checks token and video id
```

A session that holds a Facebook event refuses the simulated Messenger webhook (403), so simulated and real traffic never mix. The Page token needs `pages_show_list`, `pages_read_engagement` and `pages_read_user_content` ([permissions reference](https://developers.facebook.com/docs/permissions/)); docs used: [live video comments](https://developers.facebook.com/docs/graph-api/reference/live-video/comments/), [long-lived tokens](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived/), [rate limits](https://developers.facebook.com/docs/graph-api/overview/rate-limiting/), [error codes](https://developers.facebook.com/docs/graph-api/guides/error-handling/). Tests run against a fake Graph API (`tests/test_facebook_comments.py`); the connector has not been run against a real Page yet.

## AI disclosure

The project uses hosted models through the Meta Model API: a language model to label incoming comments, to read the product shown on camera and to read the price the seller says, and an image-segmentation model (SAM) to cut the product out of the frame for the card image. Every call is logged as a `model_call` event with its model, tokens, cost and latency. Order permissions stay in code (`orders/rules.py`); no model output can confirm an order, change a price or give a discount.

The comments used in demos and measurements are synthetic: they are simulated, not messages from real customers, and are labelled as such in `data/README.md`. Nothing in this repository sends messages to real people.

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

- The model labels both cancel comments `complaint`. Contract v1 has no cancel label and the labeling prompt says nothing about cancelling, so the label score stays 0/1. The orders stream now also reads a `complaint` label when the comment text reads as a cancel request (c192 is one): it asks the customer, once per comment, to cancel or keep the open order and never drafts an order. Only the customer's Huỷ đơn button (`CANCEL:<order_id>`) cancels; while the order is open, a plain "có" or "ok" to the question is asked again and does not confirm the order, until a button answers it or the shop sends another message in that conversation. The text check does not tell who cancelled or when, so a complaint about a cancel by the shop or an earlier cancel ("shop huỷ đơn mình rồi à?", "em huỷ đơn hôm qua rồi sao chưa hoàn tiền") may also get the question. A negated or unclear cancel labeled `complaint` (c169 is negated) sends nothing. This table is from the run before that change and was not re-run.
- c106 "size như bạn trên" was labeled `other`, so it got no order and no question. In the previous run on this row (session `comments200-s1-1027-135456`, before the cancel rows) it was an order with the item unresolved. The same comment gets different labels from run to run.
- These rows score the model's labels only. Whether the shop then asks the customer, waits for the seller or confirms a cancel is up to the orders and Messenger streams.

## Honest limits

- In `data/sessions/demo1` no model is called: the cards, labels and orders are hand-written stand-ins (`fx-` events), so the demo1 numbers check the measuring code, not the models. `uv run python -m connectors.sim.replay --session demo1 --no-fixtures` drops them.
- C8 label latency misses the 1 s target (table above). The last stage is event delivery, worker emitted → app ingested; console render time is not measured.
- The 200-comment labeled set is in `data/comments/` (how the gold is set: `data/README.md`); the Founder confirmed its review sample, c001–c030, on 27 October. It has one cancel row and one "don't cancel" row; the model labels both `complaint`. The 500-comment set is not built yet.
- Product read from the camera: the 6b prompt was tuned on 20 frames of test1, and the 351-frame gate (electronics ≥ 72/100, shoes ≥ 105/129, tops ≥ 75/93, all met) was measured on the same video, in one run (`probe-test1-6b-1950`). No second video has been measured, so 85.2 % says nothing yet about other streams. Evidence: `docs/evidence/2026-10-27/vision-test1-6b-score-1027-1950.json` (351 frames) and `vision-test1-6b20-score-1027-1947.json` (20 frames, both efforts).
- The Facebook connector reads comments only and has not been run against a real Page yet (fake Graph API in tests). Messenger is simulated: nothing is sent to real people and no real comment is hidden.
- The request and response shapes for the Model API (structured output, SAM, voice stream) come from the docs and are not yet checked against live calls.
- A Voice Transcribe realtime session lasts at most 60 minutes. Meta suggested starting a follow-on session on audio that overlaps the end of the previous one (Discord answer, 27 October). This build opens one voice session per play of a recording and has no session stitching. The planned C5 run plays the 5 min 51 s test1 recording five times in one app session (about 30 minutes), so each voice session stays far under the limit. A live stream longer than 60 minutes is not handled yet.

## Next steps

- Voice co-host: not part of P0 and not built before the 30 October gate; if C5 is met there, a narrow version for the video and demo would speak lines templated from seller-approved card data through local text-to-speech (the Model API has none), filter its own voice so it is never taken as the seller's words, have a pause button, pass every line through `orders/rules.py` and introduce itself as the shop's AI assistant.

## License

Apache-2.0 — see `LICENSE`.
