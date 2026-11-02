# Keviq Live

Keviq Live is an assistant for small sellers who sell on Facebook livestreams. While the seller shows items on camera and says their prices, it keeps a product card for the item on screen: an image of the item cut out of the video and the price the seller said. It reads the comment stream, hides comments that carry a phone number or a scam, and when a viewer asks for an item it drafts an order at the price of that item's card and asks the customer to confirm in Messenger. The order is confirmed only after the customer confirms, and the receipt carries the price of the card version the customer was quoted and, when that version has a picture, its picture. A discount, a price change or an order above 2,000,000 VND goes to the seller; no model output can confirm an order or change a price (`orders/rules.py`). In this build the video is a recording, the comments are synthetic and Messenger is simulated (see "Honest limits").

## Built during the hackathon

Everything in this repository was made during the event, from 26 October 2026 23:24 ICT; what existed before was two earlier private repositories, planning notes, the Meta Model API account and the shop's Facebook Page, and nothing from those repositories is in this one.

| Feature | Meta model | Commits | Measurement |
|---|---|---|---|
| Scaffold: event contracts, append-only store, SSE, replay on the session clock, cost-capped model client, measure | none called yet | branch `scaffold-contracts` | `make check` 58 tests; `make smoke` (real PostgreSQL) OK |
| Labeled comment stream and contact shield (`worker/brain`), 200 synthetic labeled comments | muse-spark-1.3, `reasoning.effort=minimal` | PR #3 | 200 comments at live speed: template-gold F1 0.976 (n=170), order fields 71/71, label p95 3.9 s (C8 target 1 s, not met) |
| Product read from the camera (`worker/vision/spark.py` → `product_seen`), prompt 6b | muse-spark-1.3, `reasoning.effort=minimal` | PR #15, #18, #20 | 351 frames of one test video (test1, recorded at 1080×1920 portrait, frames scored at 720×1280), one run: category right 299/351 (electronics 82/100, shoes 109/129, tops 79/93, no product 29/29), product change seen within 3 s 6/11, model_call p50/p95 1872/3168 ms, $0.8226. The prompt was tuned on 20 frames of the same video (limits below) |
| Paid replay scoring run `rescore-paid-1031-1615`: a recorded product video replayed on the session clock, 100 synthetic comments (`data/comments/c5/stream-1.jsonl`), simulated Messenger | muse-spark-1.3 (`reasoning.effort=minimal`), sam-3.1, muse-voice-transcribe-1.0 | main `bfc7611` (after PR #74, #75) | 100 comments in 6.4 min, one run (the 500-comment run is the last row of this table): order references 15/28 right, 0 on a wrong card, 13 with no card; 9 seller interventions (about 85/h); phones hidden 4/4; ask for size 1/4; comment cancel not triggered (labelled complaint); receipts with an image 0/1; `infer` p50/p95 comment label 1705/2312 ms, frame 207/254 ms, speech 1433/1489 ms, product_seen 1956/3401 ms; 499 model calls, $0.7989 by the event ledger, not reconciled with billing. Evidence: `docs/evidence/2026-10-31/rescore-paid-1031-NOTE.md` |
| Manual baseline on the same stream: one person handles stream-1 by hand while watching the same recording, with the manual-baseline kit (`measure/manual/`) | none (0 USD) | PR #79 (kit), PR #80 (evidence) | One person, one run and one stream, 6.36 min. Before the run the person had seen the assistant's aggregate results on this stream; this and the run's other departures from kit rules 1, 2 and 4 are recorded in the note. Of the 28 order comments that name an item: the person named the right item on 2, a wrong item on 3 and none on 23; the paid run above, scored the same way, got 15 right (14 orders and one question), 0 wrong and 13 with no card. Phone comments hidden: person 2 of 4, paid run 4 of 4. Evidence: `docs/evidence/2026-11-01/manual-founder-1-NOTE.md` (pull request #80) |
| Paid C5 run at full size `c5-paid-1102-1028`: the test1 recording played five times in one session, 500 synthetic comments (`data/comments/c5/stream-1.jsonl` … `stream-5.jsonl`), simulated Messenger, simulated customers and a simulated seller | muse-spark-1.3 (`reasoning.effort=minimal`), sam-3.1, muse-voice-transcribe-1.0 | main `02f6d73` (2 November), before PRs #94–#97 were merged | It ran before PRs #94–#97 were merged. A later full-size run on main `4ed3041` (`c5-paid-1102-1426`, after PR #97) had 0 of 151 comment attachments ready, and PR #102 reverted #97 (`docs/evidence/2026-11-02/c5-paid-1102-1426-NOTE.md`). Two later full-size runs that day are written up: `c5-paid-1102-1532` on main `767f7fe` (after the revert) had 137 of 151 comment attachments ready and 0 of 6 receipts with the card image (`docs/evidence/2026-11-02/c5-paid-1102-1532-NOTE.md`); `c5-paid-1102-1709` on main `31e7d8a` (after PR #103) had 9 of 9 receipts with the card image but 97 of 151 comment attachments ready (`docs/evidence/2026-11-02/c5-paid-1102-1709-NOTE.md`). One run, 500 comments in 32.6 min of active time. The five tiers on the 150 order comments of the gold, each counted on its own: purchase intent 149, right item 69, order created 70 (68 in the chain), confirmed by the customer 6, receipt with the right card version and its image 0 (right version 6/6, image 0/6). The confirmed tier is bounded by the data: the simulated customers have 10 scripted confirm lines for the 500 comments. Seller interventions 40 (73.68/h; the target of 10/h is not met). 2345 model calls, $4.2429 by the event ledger, not reconciled with billing. Evidence: `docs/evidence/2026-11-02/c5-paid-1102-1028-NOTE.md` |

**Before the event** (before 26 October 23:24 ICT; none of it is in this repository):
- Two private repositories by the author: the earlier product repository, with a prototype replay console (worked on until the week of 25 October; it is not shown in the demo or the video), and a pre-event baseline repository (one commit, pushed 26 October 20:01 ICT). During development, a file-inspection command displayed a Makefile from an earlier preparation repository. The claim that this implementation did not use that output is self-reported and has not been independently established.
- Planning notes written on the afternoon of 26 October: a plan that separates existing work from event work, and a baseline task note. They are documents, not code.
- Accounts and know-how: the Meta Model API account and its credit, the shop's Facebook Page, and the author's experience of selling on livestreams and of the order flow.

**During the event** (from 26 October 23:24 ICT):
- All code, contracts, tests, data and docs in this repository. The first commit is dated 27 October 10:22 ICT, and no commit is older than the event start: `git log --format=%aI%n%cI | sort | head -1`.
- Outside this repository: the test1 recording (27 October, kept local and out of Git), the project brief with criteria C1–C11 and the day-1 prompt (27 October morning), and a private UI mock-up (27 October) that is kept apart; nothing from the mock-up was copied here.

AI coding assistants were used as tools. The entrant is the author, working alone. As of 2 November, GitHub records a review on 75 of the 94 merged pull requests (as a pull request review, or as a comment that gives the review or its verdict); not every review was posted on the pull request, and #73, #74, #78 and #79 went into main on the author's tests, with no verdict posted on the pull request. Some branch names carry an assistant's default prefix: `claude/` on 19 merged PRs (all within #2–#29 and #62–#67) and on closed draft PR #71, and `codex/` on PR #47 and on draft PRs #48–#50, which were closed unmerged and reopened on tool-free branches as #53–#55 (#53 was merged; #54 and #55 were closed). Those prefixes are the default branch names of the coding tools used during the hackathon. The current convention: a pull request is merged under its written title, and commits carry the author's private GitHub address.

## Run it yourself

Hosted demo: optional; include a link and access instructions only if deployed.

`make check` on a fresh clone skips 7 tests that need the local test1 files in `data/local/test1/` (not in this repository; five in `tests/test_vn_test1.py`, one in `tests/test_c5_catalog.py`, one in `tests/test_c5_streams.py`). On 2 November `make check` gave `1934 passed, 7 skipped` at `ae4bbdd`, the head of PR #103, whose tree is the same as main `31e7d8a` (PR #103 description). With the folder present nothing is skipped: on 31 October at `4c53e72` the run gave `1489 passed` (`docs/evidence/2026-10-31/e2e-A-1031-0755-make-check.log`, line 30).

Everything below runs on one machine from a fresh clone, in three modes. Modes 1 and 2 need no Meta account and no API key, and call no model. Mode 3 calls the Meta models and needs a Model API key. `docs/RUN.md` has every step with the output to expect, how to run a second copy on other ports, and the paid test1 run.

| Mode | Models called | Recorded, drawn or simulated | Time |
|---|---|---|---|
| 1. Replay the judge1 session | none | product sightings, cards, comment labels, orders and Messenger messages are recorded stand-ins; product pictures drawn by this repository; synthetic comments | about 5 min from a clone |
| 2. Play the judge1 video, no key | none | video drawn by this repository; synthetic comments and seller speech, typed in on the video's clock; with no key nothing is recognised, so no card or order is made | about 2 min more |
| 3. Real models | muse-spark-1.3, sam-3.1 (muse-voice-transcribe-1.0 for a video with sound) | recorded or drawn video, synthetic comments, simulated Messenger; the cards, labels and orders are model and rule output | the length of the video, plus setup |

| Tool | Tested with | Needed for |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | 0.11.18 | everything; it downloads Python 3.12 and the packages |
| Docker with Compose v2 | Docker 29.5.2, Compose v5.1.4 | PostgreSQL 17 (`deploy/compose.yml`) |
| git, make | git 2.54, GNU Make 3.81 | the commands below |
| ffmpeg and ffprobe | 9.0.2 | modes 2 and 3 only (drawing and playing a video) |

The app listens on 127.0.0.1:8000 and PostgreSQL on 127.0.0.1:55433; if a port is taken, `docs/RUN.md` ("Other ports") has the overrides. Modes 1 and 2 need no `.env` file. If you copy `deploy/.env.example` to `.env`, its placeholder `MODEL_API_KEY=replace-me` counts as no key (`meta/transport.py`), so no request is sent to the Model API.

### Mode 1: replay judge1, no API key (about 5 minutes)

```bash
git clone https://github.com/keviqlabs/keviq-live-event.git
cd keviq-live-event
uv sync                       # the first run downloads Python 3.12 and the packages
make infra-up                 # PostgreSQL 17 in Docker on 127.0.0.1:55433, migrations applied
make app                      # terminal 1: the app on http://127.0.0.1:8000; keep it open
make replay SESSION=judge1    # terminal 2: about 65 s
```

`make replay` prints the console address of a new session as it starts:

```
replay data/sessions/judge1/events.jsonl: 69 events -> judge1-<MMDD-HHMMSS>
console: http://127.0.0.1:8000/console/judge1-<MMDD-HHMMSS>
done: 69 events posted
```

Open the console address while the replay runs. Over 62 seconds on the session clock you see product cards with their pictures and the prices the seller said, 14 comments with their labels (two hidden), six orders moving through their states, the messages sent to customers, two confirmed orders with receipts and two orders waiting in the seller's queue. "What the judge1 replay shows" below tells it step by step. `make measure-quick` prints the C1 / C3 / C5 / C8 / cost table of `data/sessions/demo1`, whose events judge1 copies; `docs/RUN.md` has the command that reads judge1 itself and its output. Over these stand-ins the table checks the measuring code, not the models.

What is recorded or simulated in mode 1: no model is called. judge1 is the hand-written session `data/sessions/demo1` with product pictures drawn by `scripts/judge_draw.py` (flat drawings, not photos); its 4 product sightings and 2 customer replies are written by hand, and its cards, comment labels, orders and Messenger messages are recorded stand-ins (the 46 `fx-` events in `data/sessions/judge1/events.jsonl`), written by hand to show the flow, not model output. The comments are synthetic and nothing is sent to anyone. What runs is the app itself: the event store, the session clock, the API and the console. The console's simulated customer buttons ("Customer agrees (sim)") post a customer reply, but the order rules leave recorded orders alone, so they do not change the orders of this replay.

### Mode 2: play the judge1 video, no API key (about 2 more minutes)

With `make app` still running:

```bash
make worker                   # terminal 2: the workers; with no key they call no model
make judge-video              # draws data/local/judge1/judge1.mp4: 64 s, 720x1280, no sound (about 15 s)
make media SESSION=judge1-video VIDEO=data/local/judge1/judge1.mp4 MEDIA_ARGS=--no-voice   # terminal 3: 64 s
make judge-feed SESSION=judge1-video   # terminal 4, right after: waits for the first frame, then about 60 s
```

`make judge-feed` prints `session judge1-video: 17 comments and speech lines of judge1, clock started …`, the console address of `judge1-video` and `done: 17 events posted`. The video is not kept in Git; `make judge-video` draws it again from `scripts/judge1.py`. `make media` plays it into the session one frame a second on the session clock, and `make judge-feed` posts judge1's 14 synthetic comments and the seller's 3 speech lines at their own times on that clock (the video has no sound, so the speech is typed in).

What you get with no key: the session receives 64 frames, 14 comments and 3 speech lines. The comment with a phone number and a link is hidden by the contact shield, a regular expression that runs before any model; every other comment is labelled `other`, and the worker logs `MODEL_API_KEY is not set` instead of calling a model. No product is recognised, so no card and no order is made. This mode shows the plumbing a paid run uses; mode 3 is the same commands with a key.

### Mode 3: real models (paid)

Variables, in `.env` at the repository root (copy `deploy/.env.example`; names only here, never commit values):

- `MODEL_API_KEY`: a Meta Model API key. This is the only one you must set.
- `MODEL_DAILY_CAP_USD` (30), `MODEL_DAILY_WARN_USD` (20), `MODEL_TOTAL_CAP_USD` (300): calls stop when the spend recorded in your database reaches the daily or the total cap.
- `MODEL_SESSION_CAP_USD`, `MODEL_SESSION_WARN_USD` (optional, off when unset; the example file suggests 8 and 6): a cap for one session. Calls already in flight when a cap is crossed still complete.
- `CATALOG_PATH` (optional): the product catalog; `data/catalog.json` by default, which has 20 items and includes the four judge1 products.

Every call is logged as a `model_call` event (model, purpose, tokens, cost, latency); `GET /api/cost?session=<id>` shows the spend. Then restart `make app` and `make worker` so they read `.env`, and run mode 2 again under a new session id:

```bash
make media SESSION=judge1-paid VIDEO=data/local/judge1/judge1.mp4 MEDIA_ARGS=--no-voice
make judge-feed SESSION=judge1-paid
```

With a key, the workers read the product in each frame (muse-spark-1.3), cut it out for the card picture (sam-3.1), read the price from the seller's speech, label the comments, and the order rules draft orders from them. This mode was run with a key on the judge1 video in six sessions on 2 November (`judge-paid-1102-1109`, `-1459`, `-1605`, `-1644`, `-2006` and `-2131` in the ledger) and one on 3 November (`judge-paid-1103-0019`); five of them are written up. Session `judge-paid-1102-1109` on main `aa457d9`: 51 model calls, $0.0879 by the event ledger; 4 cards, and of the 7 order comments 2 were handled as expected, 3 bound to the wrong card and 2 got no order and no reply; no receipt, because judge1 has no customer replies. Result: FAIL. After D-038 (PR #96) and the colour check of PR #100, the same flow ran again as session `judge-paid-1102-1644` on main `46a0f0e`: 52 model calls, $0.0747; of the 7 order comments 3 were handled as expected, 1 went to the right card in another state, 2 still bound to the wrong card (the comment points at the previous card while the new one is still a draft) and 1 got no order; no receipt. Result: still FAIL. After PR #105 (D-040), session `judge-paid-1102-2006` on main `137ffca`: 50 model calls, $0.0701; of the 7 order comments 3 were handled as expected, 1 went to the right card in another state, 1 bound to the wrong card (an item that was put away without ever having a price), 1 got a size question instead of a question about which item, and 1 got no order; no receipt. Result: still FAIL. After PR #106 (D-041), session `judge-paid-1102-2131` on main `1a886f6`: 44 model calls, $0.0663; of the 7 order comments 4 were handled as expected, 2 went to the right card in another state and 1 (cm-14, "three red bags") got no order; right card 6 of 7; only one of four items got a price, no card has an image, no receipt. Result: still FAIL. The newest session, `judge-paid-1103-0019` on main `2a3ba2e` (after PR #108, D-042): 46 model calls, $0.0676; of the 7 order comments 5 were handled as expected and 2 went to the right card in another state, none to a wrong card and none without an order; right card 7 of 7; but again only one of four items got a price, no card has an image, two customer questions written before the first card got no reply, and there is no receipt. Result: still FAIL. Between 2131 and 0019 the right-card count went from 6 to 7 of 7; that is one run each, and the cause is not established. Evidence: `docs/evidence/2026-11-02/judge1-demo1-paid-1102-NOTE.md`. The measured paid runs played the test1 recording (5 min 51 s, a seller showing ten items) with `data/comments/c5/stream-1.jsonl` (one play) or all five streams (five plays) and `CATALOG_PATH=data/comments/c5/catalog.json`; the recording is not in this repository. Where it can be downloaded and how the data files match it: `data/judge/README.md`. Its commands: `docs/RUN.md`.

### Evidence of real model calls

Each row comes from the stored `model_call` events of one run with a key.

| Run | Date | Model calls | Cost by the event ledger | Evidence |
|---|---|---|---|---|
| Paid C5 run at full size `c5-paid-1102-1426`: test1 recording five times, 500 synthetic comments, main `4ed3041` | 2 November 2026 | 2565: `product_seen` 1755 (70 of them cancelled), `label_comments` 377, `card_from_speech` 296, `card_cutout` 132, `transcribe` 5 | $4.0771, not reconciled with billing | `docs/evidence/2026-11-02/c5-paid-1102-1426-NOTE.md` |
| Paid C5 run at full size `c5-paid-1102-1028`: test1 recording five times, 500 synthetic comments, main `02f6d73` | 2 November 2026 | 2345: muse-spark-1.3 2297 (`product_seen` 1644, `label_comments` 390, `card_from_speech` 263), sam-3.1 `card_cutout` 43, muse-voice-transcribe-1.0 `transcribe` 5 | $4.2429, not reconciled with billing | `docs/evidence/2026-11-02/c5-paid-1102-1028-NOTE.md` (the call log stays out of Git; its sha256 is in the note) |
| judge1 video with real models, session `judge-paid-1102-1644`, main `46a0f0e` | 2 November 2026 | 52: `product_seen` 34, `label_comments` 13, `card_from_speech` 3, `card_cutout` 2 | $0.0747 | `docs/evidence/2026-11-02/judge1-demo1-paid-1102-NOTE.md` (section "Later run: judge-paid-1102-1644") |
| judge1 video with real models, session `judge-paid-1102-2006`, main `137ffca` | 2 November 2026 | 50: `product_seen` 30, `label_comments` 13, `card_cutout` 4, `card_from_speech` 3 | $0.0701 | `docs/evidence/2026-11-02/judge1-demo1-paid-1102-NOTE.md` (section "Later run: judge-paid-1102-2006") |
| judge1 video with real models, session `judge-paid-1102-2131`, main `1a886f6` | 2 November 2026 | 44: `product_seen` 24, `label_comments` 14, `card_cutout` 4, `card_from_speech` 2 | $0.0663 | `docs/evidence/2026-11-02/judge1-demo1-paid-1102-NOTE.md` (section "Later run: judge-paid-1102-2131") |
| judge1 video with real models, session `judge-paid-1103-0019`, main `2a3ba2e` | 3 November 2026 | 46: `product_seen` 27 (1 cancelled), `label_comments` 13, `card_cutout` 4, `card_from_speech` 2 | $0.0676 | `docs/evidence/2026-11-02/judge1-demo1-paid-1102-NOTE.md` (section "Later run: judge-paid-1103-0019") |
| judge1 video with real models, session `judge-paid-1102-1109`, main `aa457d9` | 2 November 2026 | 51: muse-spark-1.3 48 (`product_seen` 31, `label_comments` 14, `card_from_speech` 3), sam-3.1 `card_cutout` 3 | $0.0879 | `docs/evidence/2026-11-02/judge1-demo1-paid-1102-NOTE.md` |
| Paid replay scoring run `rescore-paid-1031-1615`: test1 recording, 100 synthetic comments, main `bfc7611` | 31 October 2026 | 499, none with an error: muse-spark-1.3 487 (`product_seen` 350, `label_comments` 80, `card_from_speech` 57), sam-3.1 `card_cutout` 11, muse-voice-transcribe-1.0 `transcribe` 1 | $0.7989, not reconciled with billing | `docs/evidence/2026-10-31/rescore-paid-1031-1615-model-calls.jsonl` (one line per call), `docs/evidence/2026-10-31/rescore-paid-1031-NOTE.md` |
| Comment labels, 200 synthetic comments, session `comments200-s1-1027-123307` | 27 October 2026 | 199, muse-spark-1.3 | $0.2578 | `docs/evidence/2026-10-27/labels-comments200-s1-1027-123307.json` |
| Comment labels against the approved gold, session `comments200-v2-1027-141048` | 27 October 2026 | 198, muse-spark-1.3 | $0.2589 | `docs/evidence/2026-10-27/labels-comments200-v2-1027-141048.json` |
| Product read from 351 frames of test1, prompt 6b | 27 October 2026 | 351, muse-spark-1.3 | $0.8226 | `docs/evidence/2026-10-27/vision-test1-6b-score-1027-1950.json` |

How the pieces fit: `docs/architecture.md`. Why: `docs/DECISIONS.md`.

## What the judge1 replay shows

`data/sessions/judge1/events.jsonl` is a 62-second session: 69 events, 14 synthetic comments, three lines of the seller's speech and hand-written stand-ins for the model output. In order, on the session clock:

1. **Right item, right price.** The seller holds up a red cross-body bag and says "Túi da đỏ đeo chéo sáu trăm chín mươi nghìn" (24 s). The bag's card (`TUI-03`) gets version 2: 690,000 VND and the bag's picture.
2. **An order at the card price.** A viewer writes "Chốt túi đỏ 1 cái" (28 s). Order `o-06` is drafted at the card price, 690,000 VND, and the customer gets a "please confirm" message in simulated Messenger.
3. **Only the customer confirms.** The customer answers yes (34 s) and `o-06` is confirmed. Before that it could not be: `orders/rules.py` has no path to a confirmed order without the customer's yes.
4. **Receipt.** The confirmed order carries a receipt from the card version the customer was quoted: card `card-b` version 2, its picture and 690,000 VND. In judge1 the picture is a drawing made by `scripts/judge_draw.py`, not a cut-out from a camera.
5. **An item from before.** After the brown boots' card goes live (46 s), "Lấy cái màu đỏ lúc nãy" ("the red one from before", 47 s) becomes order `o-08` for the bag, from the bag's card, not the boots'.
6. **Not sure, so it asks.** "Mua cái áo này" ("I'll buy this top", 50 s) matches no card (no top is shown in this session), so no order is made; the customer is asked "Bạn muốn lấy món nào ạ?" ("Which item would you like?").
7. **The seller decides.** "Lấy đôi vừa rồi, size 39, hai đôi giảm còn 400 được không?" asks for a discount, and "Lấy 3 túi đỏ" comes to 2,070,000 VND, above the 2,000,000 VND threshold; both orders (`o-05`, `o-14`) wait in the seller's queue instead of going to the customer.
8. **Hidden.** "Inbox 0900 000 012 lấy giá sỉ, link bit.ly/gia-si-xyz" (a phone number and an unknown link) and "Follow page em nhận quà free nhé" (promo bait) are hidden.

## Models and what each one does

The model names are the defaults in `meta/client.py`; every call goes through that client, which logs it as a `model_call` event and checks the spend caps first.

| Model | Purpose in the `model_call` event | What it does | Code |
|---|---|---|---|
| muse-spark-1.3 | `label_comments` | labels each comment (order, question, complaint, scam, spam, other) and fills the order fields: item reference, size, colour, quantity | `worker/brain/labels.py` |
| muse-spark-1.3 | `product_seen` | reads the product shown in a frame: category, name, colour | `worker/vision/spark.py` |
| muse-spark-1.3 | `card_from_speech` | reads the item and the price the seller says, for the card | `worker/brain/card.py` |
| sam-3.1 | `card_cutout` | cuts the product out of the frame for the card picture | `worker/vision/sam.py` |
| muse-voice-transcribe-1.0 | `transcribe` | turns the seller's speech into text | `worker/voice/transcribe.py` |

`muse-image-1.0` is configured in `meta/client.py` but never called, and the client refuses `muse-wonder-1.0`. Before any model is called, a regular-expression shield hides comments that carry a phone number or a link. The order rules in `orders/rules.py` decide every order state; no model output can confirm an order, change a price or give a discount.

## Data

All comments and customers are synthetic; none come from real customers.

- `data/sessions/demo1/`: the hand-written 62-second session (`events.jsonl`) and its answer key (`gold.json`); catalog `data/catalog.json`.
- `data/sessions/judge1/`: demo1 with product pictures drawn by `scripts/judge_draw.py`, built by `scripts/judge1.py build`; the judge1 video is drawn by `scripts/judge1.py video` and is not stored.
- `data/comments/`: 200 synthetic labelled comments used for the label measurements below; how the gold labels were set is in `data/README.md`.
- `data/comments/c5/`: five comment streams of 100 comments (`stream-1.jsonl` … `stream-5.jsonl`), the customers' Messenger replies, the catalog of the ten items in the test1 recording (`catalog.json`) and the item on screen for each part of that recording (`windows.json`), written for the test1 recording (`data/comments/c5/README.md`).
- The test1 recording (5 min 51 s) and its local price labels are not in this repository. `data/judge/README.md` lists which files go with which input, with their checksums.
- Optional, read-only: comments of a live video on a real Page through the Graph API ("Level 2" below). The connector ran once, read-only, on 30 October, on an already-ended live video of the author's Page: 1 comment reached the app, 0 model calls (`docs/evidence/2026-11-02/facebook-readonly-1030-NOTE.md`). It has not run on a live broadcast in progress.

## Why Facebook

In Decision Lab's survey *Live Commerce Landscape in Vietnam* (September 2024, n = 700), 88 % of surveyed consumers had watched a livestream on Facebook and 77 % watch one at least weekly. The Vietnamese live-selling market across all platforms is reported at about 2.5 million sessions a month and more than 50,000 sellers (Báo Đầu tư, April 2024, citing AccessTrade). These figures and their sources are recorded in C11 of `contracts/acceptance.md`; this repository holds no data of its own on them. The parts that touch Facebook are kept apart (`connectors/facebook` reads comments; Messenger is simulated in this build), so another platform's comment reader could replace them; none is built.

## Architecture

The components (connectors, event store, workers, console) and the events between them are described in `docs/architecture.md`. The reasons behind the design are recorded in `docs/DECISIONS.md`.

## Level 2 (real Page, read-only)

`connectors/facebook` reads the comments of one live video on a Page through the Graph API and appends them to an app session as `comment` events with `source: facebook`, through the same `POST /api/sessions/{id}/events` the simulator uses; the app stays the only writer. The connector only issues GET requests (a transport guard refuses any other method before it reaches the network), sends no message, hides no comment, and keeps the Page token in the Authorization header, out of URLs, logs and error messages. Commenter ids are hashed into `user_ref`; display names are never requested.

Set in `.env` (fake values in `deploy/.env.example`): `FACEBOOK_PAGE_TOKEN`, `FACEBOOK_LIVE_VIDEO_ID`, and optionally `FACEBOOK_GRAPH_VERSION` (default `v26.0`) and `FACEBOOK_POLL_SECONDS` (default `2`). Then, with `make app` running:

```bash
make live-ingest SESSION=<session id>                                  # polls until stopped
uv run python -m connectors.facebook.ingest --session <session id> --once   # one poll, checks token and video id
```

A session that holds a Facebook event refuses the simulated Messenger route (`POST /api/sessions/{id}/sim/messenger`, 403, `app/orders_api.py`). That is the only simulated route it refuses: the events route still accepts simulated comments in the same session. The Page token needs `pages_show_list`, `pages_read_engagement` and `pages_read_user_content` ([permissions reference](https://developers.facebook.com/docs/permissions/)); docs used: [live video comments](https://developers.facebook.com/docs/graph-api/reference/live-video/comments/), [long-lived tokens](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived/), [rate limits](https://developers.facebook.com/docs/graph-api/overview/rate-limiting/), [error codes](https://developers.facebook.com/docs/graph-api/guides/error-handling/). Tests run against a fake Graph API (`tests/test_facebook_comments.py`). The connector ran once against a real Page, read-only, on 30 October: one poll on an already-ended live video of the author's Page, 1 comment reached the app, 0 model calls (`docs/evidence/2026-11-02/facebook-readonly-1030-NOTE.md`). It has not run on a live broadcast in progress.

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

The author confirmed the review sample c001–c030 on 27 October (all 30, none changed). Five template rows were replaced by seller and cancel cases, and seven rows carry the action the shop should take next (`data/README.md`). One run on that set at live speed, same settings as above, session `comments200-v2-1027-141048`: 198 calls, $0.2589, 0 errors, written to the project database so the cost cap counts it. Evidence: `docs/evidence/2026-10-27/labels-comments200-v2-1027-141048.json` (`python -m measure.labels`) and `docs/evidence/2026-10-27/measure-comments200-v2-1027-141048-142057.json` (`python -m measure`, stages), command output in PR #7.

| | result |
|---|---|
| micro-F1, author-approved sample c001–c030 | 1.000, n=30 |
| micro-F1, template gold c031–c200 | 0.959, n=170 |
| order fields all exact | 73/74 |
| `ask_customer` (c019, c022, c106): no size or item guessed | 2/3 |
| `needs_seller` (c034 over the threshold, c092 discount): order filled in right | 2/2 |
| `confirm_cancel` (c192 "huỷ giúp em đôi size 39"): labeled `other` | 0/1 |
| `none` (c169 "đừng huỷ đơn em"): labeled `other` | 0/1 |
| scams with contact hidden | 26/26 |
| label end to end p50 / p95 | 3835 / 5325 ms |
| model_call latency_ms p50 / p95 | 2804 / 4331 ms |

- The model labels both cancel comments `complaint`. Contract v1 has no cancel label and the labeling prompt says nothing about cancelling, so the label score stays 0/1. The orders stream now also reads a `complaint` label when the comment text reads as a cancel request (c192 is one): it asks the customer, once per comment, to cancel or keep the open order and never drafts an order. Only a reply naming the order cancels it (the Huỷ đơn button, `CANCEL:<order_id>`); while the order is open, a plain "có" or "ok" to the question is asked again and does not confirm the order, also when its payload carries an order_id, until a button naming an order (or "Giữ tất cả") answers it or the shop sends another message in that conversation. The text check does not tell who cancelled or when, so a complaint about a cancel by the shop or an earlier cancel ("shop huỷ đơn mình rồi à?", "em huỷ đơn hôm qua rồi sao chưa hoàn tiền") may also get the question. A negated or unclear cancel labeled `complaint` (c169 is negated) sends nothing. This table is from the run before that change and was not re-run.
- c106 "size như bạn trên" was labeled `other`, so it got no order and no question. In the previous run on this row (session `comments200-s1-1027-135456`, before the cancel rows) it was an order with the item unresolved. The same comment gets different labels from run to run.
- These rows score the model's labels only. Whether the shop then asks the customer, waits for the seller or confirms a cancel is up to the orders and Messenger streams.

## Honest limits

- In `data/sessions/demo1` no model is called: the cards, labels and orders are hand-written stand-ins (`fx-` events), so the demo1 numbers check the measuring code, not the models. `uv run python -m connectors.sim.replay --session demo1 --no-fixtures` drops them.
- C8 label latency misses the 1 s target (table above). The last stage is event delivery, worker emitted → app ingested; console render time is not measured.
- C3 (a reference such as "the red one from before" bound to the right card) misses its 0.9 target on every full-size run. The C5 score counts it as situation 3, reference accuracy over 140 order comments: 69 of 140 = 0.493 in `c5-paid-1102-1028`, 90 of 140 = 0.643 in `c5-paid-1102-1532` and 72 of 140 = 0.514 in `c5-paid-1102-1709`, all FAIL; most misses are comments that got no card (71, 48 and 68). References to the item before the current one: 7, 11 and 9 of 20. On the judge1 video, cm-08 ("the red one from before") bound to the wrong card in all three paid runs written up on 2 November (`judge-paid-1102-1109`, `judge-paid-1102-1644`, `judge-paid-1102-2006`) and to the right card in the two written up on 3 November (`judge-paid-1102-2131`, `judge-paid-1103-0019`).
- Seller interventions per hour: 40 in the full-size run `c5-paid-1102-1028`, 73.68/h, against a target of 10/h (not met); 87.90/h in `c5-paid-1102-1532` and 61.37/h in `c5-paid-1102-1709`, also not met.
- Receipt image: in that run all 6 confirmed orders got a receipt at the right card version (6/6), but none carried an image (0/6, `image_path` NOT_RUN): each order points at a card version that had no image yet, because the cut-out was queued only when a card went live. PR #97 (merged 2 November) queued the cut-out when the card opened; the next full-size run, on main `4ed3041` (`c5-paid-1102-1426`), had 0 of 151 comment attachments ready and no confirmed order, and PR #102 reverted #97. PR #103 (merged 2 November) starts the cut-out when the draft opens and posts no version for it, so the live version can carry the image. In the full-size run on main `31e7d8a` (`c5-paid-1102-1709`) all 9 confirmed orders got a receipt with the card image (9/9), but comment attachments ready fell to 97 of 151 (137 in `c5-paid-1102-1532` on `767f7fe`) and right item to 72 of 150 (90); the cause of the drop is not established (`docs/evidence/2026-11-02/c5-paid-1102-1709-NOTE.md`).
- The customer-confirmation tier is bounded by the data: the simulated customers have 10 scripted confirm lines in the 500 comments, so at most 10 of the 150 order comments can reach a confirmed order (6 did in `c5-paid-1102-1028`, 9 in `c5-paid-1102-1709`).
- The 200-comment labeled set is in `data/comments/` (how the gold is set: `data/README.md`); the author confirmed its review sample, c001–c030, on 27 October. It has one cancel row and one "don't cancel" row; the model labels both `complaint`. The 500-comment set for C5 is in `data/comments/c5/` (`stream-1.jsonl` … `stream-5.jsonl`, 5 × 100 comments).
- Product read from the camera: the 6b prompt was tuned on 20 frames of test1, and the 351-frame gate (electronics ≥ 72/100, shoes ≥ 105/129, tops ≥ 75/93, all met) was measured on the same video, in one run (`probe-test1-6b-1950`). No second video has been measured, so 85.2 % says nothing yet about other streams. Evidence: `docs/evidence/2026-10-27/vision-test1-6b-score-1027-1950.json` (351 frames) and `vision-test1-6b20-score-1027-1947.json` (20 frames, both efforts).
- The Facebook connector reads comments only. It ran once, read-only, on 30 October, on an already-ended live video of the author's Page (1 comment reached the app, 0 model calls); it has not run on a live broadcast in progress. Messenger is simulated: nothing is sent to real people and no real comment is hidden.
- The request and response shapes for the Model API (structured output, SAM, voice stream) come from the docs; calls with them were made live in the paid runs listed in "Evidence of real model calls" (for example 499 calls without an error on 31 October, `docs/evidence/2026-10-31/rescore-paid-1031-1615-model-calls.jsonl`).
- A Voice Transcribe realtime session lasts at most 60 minutes. Meta suggested starting a follow-on session on audio that overlaps the end of the previous one (Discord answer, 27 October). This build opens one voice session per play of a recording and has no session stitching. A C5 run plays the 5 min 51 s test1 recording five times in one app session (about 30 minutes), so each voice session stays far under the limit. A live stream longer than 60 minutes is not handled yet.
- The video is a recording or a drawing played from a file: the media worker reads finished files only, so there is no live camera and no stream input. RTMP ingest is not built; the `mediamtx` service in `deploy/compose.yml` is an unused profile.
- The comments are synthetic in every mode and in every measurement in this README; Messenger is simulated in every mode.
- The judge1 pictures and video are flat drawings made by `scripts/judge_draw.py`, not photos, and in the judge1 replay the cards, labels and orders are recorded stand-ins, as in demo1. With no key, the judge1 video makes no card and no order (mode 2 above).
- The voice co-host ("Next steps") is not in this build.
- Eight concurrent vision reads per session (`VISION_READS_PER_SESSION`, default 8, D-042) rest on one paid C5 run with eight (`c5-paid-1102-2141-r8`) and one earlier run with four (`c5-paid-1102-1709`), same code and recording otherwise, and no control run at the same time. Better with eight: p95 time from frame to product event 56.4 s → 11.7 s, right item for 86 instead of 72 of 140 order comments (reference accuracy 0.514 → 0.614 against 0.9, still a fail), latest attachment expired 23 instead of 54 times. Worse with eight: each read took longer (product_seen p50 2.4 s → 2.9 s) and 176 instead of 129 of 1755 reads were cancelled at the 10 s limit; ledger cost 4.25 → 4.37 USD, cause not established; seller interventions 34 → 49 (61.4 → 91.7 per hour against a target of 10, so C5 still fails); confirmed orders with a receipt 9 → 7. The replay test (`tests/test_worker_vision_readers_replay.py`) shows the queue only: with the recorded read times, frames wait up to 96.6 s for a read with four readers and up to 7.9 s with eight; slower reads under more load are not in it.

## Next steps

- Voice co-host: not part of P0 and not in this build. A narrow version for the video and demo would speak lines templated from seller-approved card data through local text-to-speech (the Model API has none), filter its own voice so it is never taken as the seller's words, have a pause button, pass every line through `orders/rules.py` and introduce itself as the shop's AI assistant.

## License

Apache-2.0 — see `LICENSE`.
