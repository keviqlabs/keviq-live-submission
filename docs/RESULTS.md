# Results

This page puts the measured runs side by side, says what limits their numbers, gives the one clean-clone timing, and
lists the work that is not merged. Every run here is one run (n = 1 per configuration) on synthetic comments. Numbers
recounted on 3 November, with the command and output for each, are in
`docs/evidence/2026-11-03/results-1103-NOTE.md`; the others are copied from the run notes named in each row.

Every full-size C5 run in the table fails C5: in all three, the right item (reference accuracy, target 0.9) and the
seller interventions (target 10 per hour) miss their targets (column "Verdict" and the run notes cited there). Both
judge1 runs fail the video test flow.

## The runs

Two kinds of run:

- **Full-size C5 run.** The test1 recording (5 min 51 s, not in this repository) played five times, 1755 frames, with
  the 500 synthetic comments of `data/comments/c5/stream-1.jsonl` … `stream-5.jsonl`, a simulated seller and the
  simulated customer replies of `data/comments/c5/messenger-*.jsonl`. It is scored in five tiers: purchase intent →
  right item → order created → confirmed by the customer → receipt with the right card version and its image.
- **judge1 with real models.** The judge1 video once, 14 synthetic comments of which 7 are order comments, no
  customer replies, no seller action during the run (`docs/evidence/2026-11-02/judge1-demo1-paid-1102-NOTE.md`:
  14 comments, line 211; 7 order comments, line 232; no seller acted, line 253; no customer replies, line 256).

| Session | Code head | Configuration | Sample | Right item | Orders | Confirmed | Receipts | Expired attachments | Cancelled vision reads | Seller interventions | Ledger USD (calls) | Verdict | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `c5-paid-1102-1532` | `767f7fe` | full-size C5; 4 vision reads at once is the default at this head (`worker/runner.py` line 40); whether the run set the variable is not recorded | 150 order comments; right item counted over 140 | 90 of 140 (0.643) | 93 | 6 | 6, 0 with an image | 14 of 151 | 17 of 1755 | 47 = 87.90 / h | 3.97704250 (2484) | FAIL: situations 3, 4, 5; interventions above 10 / h | `docs/evidence/2026-11-02/c5-paid-1102-1532-NOTE.md`, lines 9-12, 40-41, 54-61, 65, 75; recount: `results-1103-NOTE.md`, "Ledger" |
| `c5-paid-1102-1709` | `31e7d8a` | full-size C5; 4 vision reads at once (D-042, "Before") | as above | 72 of 140 (0.514) | 72 | 9 | 9, 9 with an image | 54 of 151 | 129 of 1755 | 34 = 61.37 / h | 4.25326475 (2536) | FAIL: situations 3, 4, 5; interventions above 10 / h | `docs/evidence/2026-11-02/c5-paid-1102-1709-NOTE.md`, lines 11-13, 46-58, 71-78, 82, 92; recount: `results-1103-NOTE.md`, "Ledger" |
| `c5-paid-1102-2141-r8` | `31e7d8a` | full-size C5; `VISION_READS_PER_SESSION=8`, nothing else changed (D-042, "After") | as above | 86 of 140 (0.614), not re-derived | 85 distinct orders in the ledger | 7 | 7, 7 with an image | 23 of 152 | 176 of 1755 | 49 = 91.7 / h, not re-derived | 4.36595840 (2552) | FAIL: reference accuracy and intervention rate (D-042); the other situations are not in this repository | `docs/DECISIONS.md`, D-042 (lines 16-27); recount: `results-1103-NOTE.md`, "Ledger" and "Not re-derived here" |
| `judge-paid-1102-2131` (context) | `1a886f6`, by the run record | judge1 video; 4 vision reads at once is the default at this head (`worker/runner.py` line 40); whether the run set the variable is not recorded | 7 order comments | right card 6 of 7, counted by hand | 5 | 0 | 0 | no attachment events | 0 of 24 | 0 (no seller acted) | 0.06632745 (44) | FAIL (video test flow) | `docs/evidence/2026-11-02/judge1-demo1-paid-1102-NOTE.md`, "Later run: judge-paid-1102-2131", lines 180-203 (seller interventions 0 by `measure`, line 200; the 10 order events are the 5 orders' drafts and decisions, line 182); recount: `results-1103-NOTE.md`, "Ledger", lines 217-257 (170 events, 5 orders, 0 receipts, 0 confirmed) |
| `judge-paid-1103-0019` | `2a3ba2e`, by the run record | judge1 video; 8 vision reads at once is the default at this head; whether the run set the variable is not recorded | 7 order comments | right card 7 of 7, counted by hand | 6 | 0 | 0 | no attachment events | 1 of 27 | 0 (no seller acted) | 0.06761595 (46) | FAIL (video test flow) | `docs/evidence/2026-11-02/judge1-demo1-paid-1102-NOTE.md`, "Later run: judge-paid-1103-0019", lines 210-258; recount and offline `measure`: `results-1103-NOTE.md`, "Ledger" and "Offline measure" |

Notes on the table:

- Ledger USD is the sum of `cost_usd` over the session's `model_call` events. It is not reconciled with billing.
- "Confirmed" is the count of orders whose last state is CONFIRMED in the ledger; for `c5-paid-1102-1532` and
  `-1709` it equals the score's confirmed tier (6 and 9, the NOTE lines cited).
- "Orders" for `c5-paid-1102-1532` and `-1709` is the score's "order created" tier, which equals the ledger's distinct
  `order_id` count (93 and 72). For `-2141-r8` only the ledger count (85) was taken; the score file of that run is not
  in this repository.
- Receipts for `-2141-r8` were counted in the ledger (7, all with an `image_url`); whether each carries the right
  card version was not checked for that run.
- The two judge1 runs have no receipts and no confirmed orders because the judge1 feed has no customer replies
  (judge1 NOTE, lines 201 and 256). Their
  seller interventions are 0 because no seller acted, not because no order needed the seller: five of the six
  orders of `judge-paid-1103-0019` wait in NEEDS_SELLER (judge1 NOTE, line 232).
- Earlier full-size runs of 2 November, outside the table: `c5-paid-1102-1028` on `02f6d73`
  (`docs/evidence/2026-11-02/c5-paid-1102-1028-NOTE.md`) and `c5-paid-1102-1426` on `4ed3041`, where all 151
  attachments expired (`docs/evidence/2026-11-02/c5-paid-1102-1426-NOTE.md`, "Outcome").

## What limits these numbers

- **Sample.** Of the 150 C5 order comments, 140 name an item and point at the current or previous card; the right
  item is scored over those 140 (`counted()` in `measure/c5_score.py`, lines 42-45). The other 10 name no item
  (gold `reference_kind` unresolved, expected action `ask_customer`) and are not in that denominator. Recount:
  `results-1103-NOTE.md`, "Recount of the data files".
- **Scripted replies.** The simulated customers have 20 scripted replies across the five streams. 5 wait for an
  `ask_cancel` message and are the CANCEL quick reply. 15 wait for a `confirm_order` message: 10 are the CONFIRM
  quick reply, which the script expects to end in CONFIRMED (situation 12), and 5 are a plain-text yes sent after the
  card's price changed, which the script expects to end in NEEDS_SELLER (situation 10). By the script's
  expectations, 10 of the 150 order comments reach confirmed and receipt; no order is CONFIRMED without the
  customer's confirmation (`AGENTS.md`, rule 6). The 6, 9 and 7 above are counts to set against those 10 expected,
  not rates over 150.
  Recount: `results-1103-NOTE.md`, "Recount of the data files"; the reply loop that waits for the shop's message:
  `measure/c5_shop.py`, lines 325-333.
- **Two plain-text confirmations in `c5-paid-1102-1709`.** In `c5-paid-1102-1532` (6) and `-2141-r8` (7) every
  CONFIRMED order follows its own CONFIRM quick reply. In `-1709`, 7 of the 9 do. The other 2, `o-c5-3-083` and
  `o-c5-4-005`, became CONFIRMED (actor customer, reason "customer confirmed in Messenger") right after the
  plain-text replies `c5-3-m04` and `c5-4-m01`, two of the five that the script expects to end in NEEDS_SELLER. At
  `31e7d8a` a plain-text yes confirms the thread's open order unless the card changed in price or SKU since the order
  (`orders/flow.py`, `on_reply`, lines 705-766); why no change was found for these two orders is not established,
  and whether this is a defect is not decided. In the other two runs `c5-4-m01` was sent and confirmed nothing, and
  `c5-3-m04` was not sent. Queries and output: `results-1103-NOTE.md`, "Confirmations by reply".
- **Cancel never fires.** In all five full-size runs of 2 November the shop sent no `ask_cancel` message, so the five
  scripted CANCEL replies (situation 8) were never sent. In the three C5 runs of the table the five cancel comments
  were labeled `complaint` (3 or 4 per run) or `other`, with action `none`. The shop asks only when the commenter
  has an open order and the 24 h Messenger window allows a message (`orders/flow.py`, line 310); which condition
  stopped it in these runs is not established. The CANCELLED orders of `c5-paid-1102-1532` (12) and `-1709` (8)
  match the seller's over-stock and variant cancellations in their NOTEs (10 + 2 and 6 + 2; lines 59-61 and 76-78);
  for `-2141-r8` the causes are not in this repository. The run notes record situation 8 as "not triggered"
  (`c5-paid-1102-1532-NOTE.md` line 41, `c5-paid-1102-1709-NOTE.md` line 58). Ledger: `results-1103-NOTE.md`,
  "Ledger".
- **n = 1 per configuration.** Each row is one run. No control run was made at the same time
  (`docs/DECISIONS.md`, D-042). Differences between rows, such as right item 72 → 86 of 140 or cancelled vision
  reads 129 → 176, are single observations; their causes are not established.
- **C3 0.143 on judge1 is a measuring artefact, not a reference result.** `measure` compares `referenced_card_id`
  literally with the gold ids `card-a`, `card-b`, `card-d` (`measure/core.py`, lines 93-103;
  `data/sessions/judge1/gold.json`), while a live run names its cards `card-<hash>`, so only the comment with no
  card on either side matches. The by-item count (6 of 7, then 7 of 7) is a hand count in the judge1 NOTE, line 252.
  Rerun offline with no key: `results-1103-NOTE.md`, "Offline measure".
- **Simulated parties.** The seller and the customers of the C5 runs are scripts; the comments are synthetic; the
  test1 recording is not in this repository, and where to download it is not given yet (`data/judge/README.md`,
  line 22, "TO FILL").

## Clean clone

One timed run, on 3 November at `2a3ba2e`, on one machine: from the clone to the console page of the replayed
judge1 session answering HTTP 200 with all 69 events stored took 77 s (clone 3 s, `uv sync` 1 s, `make infra-up`
6 s, app ready 3 s, `make replay SESSION=judge1` 64 s). The clone step was timed with an authenticated
`gh repo clone` of the working repository (`docs/evidence/2026-11-03/cleanclone-1103.sh`, line 16), not with the
`git clone` the README shows; `git clone` was not timed. Evidence: `docs/evidence/2026-11-03/cleanclone-1103.log`
(timing stamps on lines 2, 4, 59, 71, 72, 77 and 78) and `results-1103-NOTE.md`, "Clean clone (one run)".

What the 77 s does not include:

- pulling the PostgreSQL image, which was already on the machine (log line 1);
- downloading Python 3.12, which was already installed (log line 6); the README says a first `uv sync` downloads it;
- reading the README and opening a second terminal.

The uv package cache was emptied first, so the 38 packages were downloaded (log line 19); the network speed was not
measured. The script used its own compose project and ports (55499 and 8072) so as not to touch another database
container on the machine; the README defaults are 55433 and 8000. Most of the time is the replay, which posts the
judge1 events on their own 61.8 s clock.

## Work in progress

The published work-in-progress branch, not merged into `main` on 3 November. It is not part of the results above, and
nothing here claims it works. Other unmerged branches of the working repository belong to closed or superseded pull
requests and are not part of this work. Four other branches are not published because what they add is already on
`main`. Three of them carry the same changes, apart from comment rewording: `measure-first-hide` through PR #9
(27 October), `test/sku-ambiguity` and `test/written-phone-shield` through PR #60 (28 October). The fourth,
`returning-card-current`, makes a product shown again the current card again; `main` has done that since PR #61
(29 October), which builds it differently, and the branch's own test passes on `main`. "Last commit" is the date of the
branch's tip.

| Branch | Last commit | What it changes |
|---|---|---|
| `speech-leads-prep` | 27 October | `docs/speech-leads-prep.md`: a preparation draft, not a decision, for letting a spoken product switch trigger a one-frame vision check before a new card opens |
