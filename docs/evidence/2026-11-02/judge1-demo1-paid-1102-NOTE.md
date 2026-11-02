# Run 2 — demo1 and judge1 with real models (video test flow) — 2 November 2026

Cleaned copy of the run note kept outside the repository (source sha256
`e740e7ae148436898d41608681c419ee3523bab48c7ace4ed5572529ec647107`): local paths and internal labels are removed, the
Vietnamese comment and speech texts are replaced by English glosses, and times are in ICT. The numbers are unchanged.

## Outcome
- Code: origin/main aa457d98785a51320aa80a62c25744941b946afc (tree 6b7da8f), a clean clone outside the working copy, no local change.
- Caps as coded: session 1.5 / warn 1.2, daily 30 / warn 20, total 285. Port 8040. Database `keviq_paid_test`. The model key was read from a local `.env` and never printed.
- Two sessions as specified, plus one diagnostic extra (labeled below). All three stayed far under 1.5 USD per session.
- **Video test flow result: FAIL.** Cards appear in judge1, but 3 of 6 order comments bind to the wrong card, 2 order comments get no order and no reply (the session table below lists 7 order comments: 2 as expected, 3 on a wrong card, 2 with no order), one card carries another item's spoken price, no receipt in either specified session, and no receipt anywhere carries an image.

## Sessions
| session | kind | start ICT | end ICT | events posted | rc | session USD |
|---|---|---|---|---|---|---|
| demo-paid-1102-1107 | replay demo1 `--no-fixtures` (as specified) | 11:07:16 | 11:08:19 | 23 | 0 | 0.035989 |
| judge-paid-1102-1109 | judge-video (built) + media `--no-voice` + judge-feed | 11:09:21 | 11:10:38 | 17 feed + 64 frames | 0 / 0 / 0 | 0.087888 |
| demo-paid-1102-1112r | **diagnostic extra, not in the plan**: same replay with `CARDS_FROM_REPLAY=1` (D-017 switch) | 11:12:19 | 11:13:22 | 23 | 0 | 0.037234 |

judge1.mp4 sha256 64882a93787a1f0856886d6d538bf01537e2d1f7445b8aa3ffde3b236cd2f86a (64 s, 720x1280, no sound). App and worker stopped 11:15:36 ICT, port 8040 free.

Cost endpoint after the last session: today_usd 4.404045 (Run 1 4.242935 + Run 2 0.161111), total_usd 6.654379.

## Per session
### demo-paid-1102-1107 (as specified)
- Labels (15): order 7, price_question 2, scam 2, complaint 1, stock_question 1, spam 1, other 1. cm-04 hidden (phone + link).
- **Cards 0, orders 0, receipts 0.** message_out: ask_which_item x4 (cm-05, cm-07, cm-08, cm-09).
- Why: demo1 has no video. Replayed product_seen events carry no caused_by, and `on_product_seen` ignores them unless `CARDS_FROM_REPLAY=1` (D-017). Speech is ignored while a session has no card. Model calls: label_comments 13.

### judge-paid-1102-1109 (as specified)
- Cards (t = seconds from session start):
  - GIAY-01 sneaker: seen 0, live 450k at 8.7, image at 10.6.
  - TUI-03 bag: seen 21, draft 23.7, live 690k at 28.2, **no image** (worker log `run2-worker-part1.log:215` "sam found no handbag").
  - BOOT-02 black boot: seen 40, live at 49.2 with **920000 from speech sp-d1** (the brown boot's line; gloss: "short brown leather boot, nine hundred twenty thousand"), image v3.
  - BOOT-05 brown boot: seen at 58.0, draft at 61.3, never priced.
  - Frames: 64, product_seen calls 31, skipped 33 (`frame … skipped` lines in the worker log).
- Orders (expected = demo1 fixture intent; comment texts as English glosses):

| comment | gloss | got | expected |
|---|---|---|---|
| cm-07 | one pair, size 40 | GIAY-01 40 CONFIRM_SENT | same ✓ |
| cm-05 | the pair from just now, size 39, two pairs for 400… | TUI-03 draft, NEEDS_SELLER | GIAY-01 39, NEEDS_SELLER (discount) ✗ |
| cm-06 | confirm one red bag | GIAY-01 variant red, NEEDS_SELLER | TUI-03 CONFIRM_SENT ✗ |
| cm-08 | the red one from before | BOOT-02 draft red, NEEDS_SELLER | TUI-03 ✗ |
| cm-09 | buy this shirt | ask_which_item | same ✓ |
| cm-11 | confirm the brown boots, size 39 | **no order, no reply** | BOOT-05 39 ✗ |
| cm-14 | three red bags | **no order, no reply** | TUI-03 x3, NEEDS_SELLER ✗ |

- Receipts 0, CONFIRMED 0: the judge feed has no customer replies, so these tiers cannot be reached in judge1.
- Model calls 51: product_seen 31 (p50 2017 ms, p95 2679), label_comments 14 (p50 2107), card_from_speech 3 (p50 3647), card_cutout 3.

### demo-paid-1102-1112r (diagnostic extra)
- Cards from the replayed product_seen: GIAY-01 450k, TUI-03 690k, BOOT-02 retired (no price), BOOT-05 920k. No image on any card (replay has no frames, so no cut-out).
- Orders: o-05 TUI-03 ✗, o-06 GIAY-01 red ✗, o-07 GIAY-01 40 CONFIRM_SENT ✓, o-08 BOOT-05 ✗, o-11 BOOT-05 39 brown → **CONFIRMED by the customer reply mi-11**, cm-14 no order ✗.
- Receipt o-11: `{"price": 920000, "card_id": "card-d", "image_url": null, "card_version": 2}` — a receipt without an image.
- cm-09 ("buy this shirt"; no shirt in the catalog) was labeled current_card BOOT-05 and got ask_variant instead of ask_which_item.

## Causes and proposals
Each cause below is reproduced from stored events or logs. No fix was started in this note; each waited for approval.

**A. Order binds to a different card than the one the label model chose (cm-05, cm-06, cm-08; both sessions).**
- Evidence: offline replay of `worker/brain/labels.py` `cards_at` on the stored card events of both sessions (0 USD, `resolve_check.py` → `resolve-check.out`). For cm-05/cm-06 the model was shown current=TUI-03 (draft) / previous=GIAY-01, while `resolve()` (live_only=True when no attachment flow) used current=GIAY-01 / previous=TUI-03. "previous_card" and "current_card" therefore land on the swapped card. cm-08 shows the same swap (model: current BOOT-02 draft, previous TUI-03; resolve: current TUI-03, previous BOOT-02).
- Code: `_ask` builds the prompt with `cards_at(versions, ts)` (drafts included); `_consequences` calls `resolve(..., live_only=not attached_flow)`.
- Fix: build the prompt's current/previous with the same `live_only` that `resolve()` will use, so the model and the binder see one snapshot. File `worker/brain/labels.py`. Changes the model input, so it needs a decision entry with before/after on the same sessions. Risk: low; an order on a still-draft card keeps going to NEEDS_SELLER through the existing rules.
- Later the same day: recorded as D-038 and merged in PR #96 (merge 159c841, 2 November 2026). Measured on the judge1 video in judge-paid-1102-1644 (section "Later run" below): cm-06 now goes to TUI-03, while cm-05 and cm-08 still go to a wrong card because the comment points at the previous card while the new one is still a draft. The demo1 sessions were not written up again.

**B. An explicit product name that is not the current or previous card produces nothing (cm-14 both sessions, cm-11 judge).**
- Evidence: order_draft `{"reference_kind": "explicit_sku", "sku": "TUI-03", "referenced_card_id": null}` for cm-14; no order and no message_out follow. `resolve()` binds explicit_sku only when the SKU is the current or previous card. Otherwise it keeps explicit_sku with no card. The label stream asks only for `unresolved`. `orders/flow.py on_labeled` returns [] when there is no card id.
- Fix: for explicit_sku in the catalog, bind to the newest card of that SKU shown at or before the comment's ts_ms. If none has been shown yet, mark the draft unresolved so ask_which_item goes out. File `worker/brain/labels.py resolve()`, plus a test. Risk: low. Does not touch `orders/rules.py`. Not fixed as of this note.
- Later the same day, in judge-paid-1102-1644: cm-14 got an order on TUI-03 (NEEDS_SELLER); cm-11 still got none, with no BOOT-05 card in that session.

**C. A spoken price goes to a card whose colour the line contradicts (BOOT-02 got 920000 from the brown boot's line).**
- Evidence: card-74c6572cbe35 v2 `price 920000 caused_by ["…sp-d1"]`, where sp-d1 is "short brown leather boot, nine hundred twenty thousand" (gloss). The card is "short black leather boot" (gloss). `kind_price_reason` / `window.choose` check the kind (boot) only, not the colour.
- Fix: when the line names a catalog colour and none of the named colours is a variant colour of the card's SKU, the price waits (new wait reason `color_mismatch`). File `worker/brain/card.py`, plus a test. Risk: medium; a seller who names a wrong colour gets no auto price and the price stays manual.
- Later the same day: merged in PR #100 (merge 46a0f0e). In judge-paid-1102-1644 the brown boot's 920000 line did not go to BOOT-02, which was never priced.

**D. Receipt without an image.**
- Evidence: the TUI-03 cut-out failed ("sam found no handbag"). The replay has no frames, so it has no cut-out. Run 1: 6/6 receipts have no image (see `c5-paid-1102-1028-NOTE.md` in this folder). The o-11 receipt points at card-d v2, image_url null.
- Proposal (from the Run 1 analysis, see its "Causes and proposals"): R3, queue the cut-out at card open (`worker/brain/card.py`), so the image version exists before the price; R1 relock is D-032 bound and only a fallback; R2 SAM noun from the catalog kind for "other" items. TUI-03 "sam found no handbag" is a SAM miss on a known noun, which R3 does not fix.
- Later the same day: R3 was merged in PR #97 (merge e5328cc, 2 November 2026). The next full-size run, on main 4ed3041, had 0 of 151 comment attachments ready (`c5-paid-1102-1426-NOTE.md` in this folder), and PR #102 (merge 767f7fe) reverted #97. PR #103 (merge 31e7d8a) starts the cut-out when the draft opens without posting a card version for it, so the live version can carry the image, and adds R2. Its effect on receipts is not measured yet. <!-- run pending -->

**E. Brown boot seen 17 s late (58.0 vs about 41).**
- Evidence: 33 of 64 frames skipped while a product_seen call was in flight (p50 2.0 s per call). Why the frames that were read did not switch the card to BOOT-05 earlier is **not established** from the logs: product_seen results for those frames are not logged.
- Proposal: none yet. A frame-level log would be needed first, which is a measurement change, not a fix.

**F. judge1 cannot reach CONFIRMED or a receipt.**
- The judge feed carries no customer replies. This is a property of the test data. No product fix.

## Files (outside Git, sha256)
`run2-app-part1.log` and `run2-worker-part1.log` cover the specified sessions; `run2-app.log` and `run2-worker.log` cover the extra session. `run2-SHA256SUMS.txt` (1474 bytes, sha256 `58cb22efaf97569263fce028a2362be703d1da39d91ee54ed36275c66f2b8221`) lists every file below.

| File | Bytes | sha256 |
|---|---|---|
| `demo-paid-1102-1107-replay.log` | 156 | `efd74e12bfd87052f8c495df78f41703f47e49135e66802f96eb319222606e53` |
| `demo-paid-1102-1107-stats-final.txt` | 678 | `7777ac6033a6043d112b532f9e7fe6f6f63f03fefa04048fb5cb0545ae5e5de0` |
| `demo-paid-1102-1107-stats1.txt` | 678 | `c1c932cc7303bfce705c03dab7569befdbe6e11ca973b6c76923464479b8e397` |
| `demo-paid-1102-1112r-replay.log` | 158 | `eed157e36b4324ed963668882f8f48f361be9ad5a4fbf7403d4833a26a2081fb` |
| `demo-paid-1102-1112r-stats-final.txt` | 1386 | `991e0a70d9838fe5c6cedf2284009499dda94fe613ceadaf1debea4fe80b96cd` |
| `judge-paid-1102-1109-feed.log` | 279 | `5ecf1a9f2cae5ea232befbcb69639d4f5c3206e84806a1e967be8f110e9e52ab` |
| `judge-paid-1102-1109-media.log` | 8842 | `f973a240edf3b70ba76cdf09e180f12926f17151570ea037ea405d8a7ea1acc0` |
| `judge-paid-1102-1109-stats-final.txt` | 1410 | `5217f80ace1f1feda9c0390da9ef8a54c8aaaf7ebe7035846bb0709be15099d7` |
| `judge-paid-1102-1109-stats1.txt` | 1410 | `6b203f7777951386429a47763205b0b4981a072ef2385f353f371a3e12dbb85a` |
| `judge-video.log` | 93 | `4d7e96c38419c1ba961eaab6a8a86cac2f3c416389a49b658320b26b11ec7937` |
| `resolve-check.out` | 2439 | `3613f44a00a70ec95d8216d7214e1c096d98b820b13f8657226ef37316bfdedb` |
| `resolve_check.py` | 1032 | `03d6b5535d3b16ebbf2b13bea9cd59fc8c97365159f14f909f4d4c52ec8a5b3b` |
| `run2-app-part1.log` | 34274 | `3208bc319456962c4d0086b39051419a7f6c3c4e6aa0885fadd1a262291f9348` |
| `run2-app.log` | 11610 | `0e28f1a64cd7ff1f5598a4d8f89526dbed29b5c1a7433f952c2facdd300f659b` |
| `run2-worker-part1.log` | 46525 | `a55b4994155a19f898c2fc2e34b1c0cb4241f668bab4f7d0ddfa7955c65303aa` |
| `run2-worker.log` | 14995 | `3e84698a6e24496e3b81995e72dddb955e4f0aa48a8145cc27f75425a5bf86db` |

## Later run: judge-paid-1102-1644
Added on 2 November from a later section of the same run note (kept outside the repository), checked against
read-only `SELECT` queries on `keviq_paid_test`. Times are in ICT; t = seconds from the session's first event.

- Code: origin/main 46a0f0ebd40e740484ca3c4bf4ebb6531e685184 (tree 6b163d4), the merge of PR #100 "Hold a heard price when the line names a colour the card does not have". It includes D-038 (PR #96), PR #98, #99 and #102 (which reverted #97); it does not include PR #103.
- Same kind of session as judge-paid-1102-1109: judge-video + media `--no-voice` + judge-feed, database `keviq_paid_test`, port 8040.
- Events stored 16:45:06–16:46:13: 176 (frame 64, model_call 52, comment_labeled 15, comment 14, order 10 = 5 orders × draft and decision, message_out 9, card 6, product_seen 3, speech 3).
- Cards:
  - GIAY-01 sneaker: draft 2.8, live 450k at 8.7, image at 10.4.
  - TUI-03 bag: draft 22.4, live 690k at 28.2, **no image**.
  - BOOT-02 black boot: draft 41.7, **never priced**. The brown boot's 920000 line is now held instead of going to the black boot (in judge-paid-1102-1109 BOOT-02 went live at 920000).
  - BOOT-05 brown boot: no card.
- Labels (15 for 14 comments): order 7, price_question 2, scam 2, stock_question 1, spam 1, complaint 1, other 1. cm-04 was labeled twice (scam, spam) and hidden; cm-12 (scam) was warned.
- Replies (message_out 9): price for cm-01 and stock for cm-02 (GIAY-01); order received for cm-05, cm-06, cm-08, cm-14; order confirmation for cm-07; ask_which_item for cm-09 (TUI-03 / BOOT-02); "the seller will confirm the price" for cm-13 (BOOT-02).
- Orders (same comments and expectations as judge-paid-1102-1109 above):

| comment | gloss | got | expected |
|---|---|---|---|
| cm-07 | one pair, size 40 | GIAY-01 40 CONFIRM_SENT | same ✓ |
| cm-05 | the pair from just now, size 39, two pairs for 400… | TUI-03 draft, size 39 x2, NEEDS_SELLER | GIAY-01 39, NEEDS_SELLER (discount) ✗ |
| cm-06 | confirm one red bag | TUI-03 red x1, NEEDS_SELLER (the card was still a draft when the comment was written; TUI-03 went live at 28.2) | TUI-03 CONFIRM_SENT: right card, other state |
| cm-08 | the red one from before | BOOT-02 draft red, NEEDS_SELLER | TUI-03 ✗ |
| cm-09 | buy this shirt | ask_which_item (TUI-03 / BOOT-02) | same ✓ |
| cm-11 | confirm the brown boots, size 39 | **no order** (no BOOT-05 card) | BOOT-05 39 ✗ |
| cm-14 | three red bags | TUI-03 red x3, NEEDS_SELLER (2,070,000 over the 2,000,000 seller threshold) | same ✓ |

- Of the 7 order comments: as expected 3 (cm-07, cm-09, cm-14); right card in another state 1 (cm-06); wrong card 2 (cm-05, cm-08: the comment points at the previous card while the new one is still a draft); no order 1 (cm-11).
- Against judge-paid-1102-1109: cm-06 and cm-14 now land on TUI-03; cm-05 and cm-08 are still on a wrong card; cm-11 still has no order.
- Receipts 0, CONFIRMED 0: the judge feed has no customer replies.
- Model calls 52, 0.0747279 USD (`SELECT` on `model_call`): product_seen 34 (p50 1598 ms, p95 3390), label_comments 13 (p50 1849, p95 2823), card_from_speech 3 (p50 3398, p95 3479), card_cutout 2 (789 and 1468 ms). Percentiles are nearest rank (`percentile_disc`).
- **Result: still FAIL** (2 wrong cards, 1 missing order, no receipt).
