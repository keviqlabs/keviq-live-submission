# Evidence index

One row per NOTE under `docs/evidence/2026-10-27` to `docs/evidence/2026-11-03`. Each NOTE names its run, its
commit and its files; the numbers here are copied from the NOTE and are not re-measured. Cost is the USD the
NOTE states from the ledger (`model_call` events); "not stated" means the NOTE gives no USD figure.

| Date | Run | NOTE | What it measured | Result | Cost |
|---|---|---|---|---|---|
| 2026-10-27 | `replay-pp-test1-1027-2244-A` / `-B` | [price-proposal-replay-NOTE.md](2026-10-27/price-proposal-replay-NOTE.md) | Replay of test1 (D-024): do the 3 real price changes become a `price_proposal` on the right card, and how many proposals carry a later product's price on an earlier card | 11 (A) and 12 (B) proposals; 4 on the same product; 7 (A) and 8 (B) carry a later product's price on an earlier card | 0 USD |
| 2026-10-27 | `probe-test1-speech64-2141-card` / `-nocard` | [speech64-card-line-NOTE.md](2026-10-27/speech64-card-line-NOTE.md) | Whether the speech prompt needs the card line to read the spoken price, on the 64 test1 voice lines | Both variants 22 / 22 correct on the gold lines, so the card line is dropped; card p50/p95 4372/6292.5 ms, nocard 3690/5351.05 ms | 0.37888290 USD (128 calls) |
| 2026-10-27 | `probe-test1-6b-1950` | [vision-test1-6b-NOTE.md](2026-10-27/vision-test1-6b-NOTE.md) | Scope of the 6b vision prompt evidence: tuned on 20 frames, gated on the 351 frames of the same video | One video, one run; no second video measured; JSON files unchanged since written | not stated |
| 2026-10-28 | `c5-dry-1028-024114` (rescored) | [c5-dry-1028-024114-rescore-NOTE.md](2026-10-28/c5-dry-1028-024114-rescore-NOTE.md) | One dry C5 loop (gold labels, stored vision rows, rule price reader), rescored with the fixed scorer | Order recall 1.0 on 30 gold comments; reference correct 12 / 28; 9 of 10 items got a card; 28 interventions; situations 1–5 FAIL, 6–10 and 12 PASS | 0 USD |
| 2026-10-28 | `c5-dry-1028-040116` | [c5-dry-1028-040116-NOTE.md](2026-10-28/c5-dry-1028-040116-NOTE.md) | One new dry C5 loop after the review fixes to the scripted shop and the definitions | Reference correct 12 / 28; 22 interventions (208.9 per hour); 8 proposals, right card 7; situation 2 now PASS, 1, 3, 4, 5 FAIL | 0 USD |
| 2026-10-28 | `c5-dry-1028-040116` (rescored) | [c5-dry-1028-040116-rescore-NOTE.md](2026-10-28/c5-dry-1028-040116-rescore-NOTE.md) | The same session scored again after the ruling on stale price proposals | Handling median 4052 ms (n 4) → 4066 ms (n 3); one stale proposal; every other number unchanged | 0 USD |
| 2026-10-28 | `c5-test1x5-1028-1000` | [c5-test1x5-1028-1000-NOTE.md](2026-10-28/c5-test1x5-1028-1000-NOTE.md) | Paid live C5 run, 5 loops of test1 on main `51e3b6d`, baseline before the current-item rule | Runner rc 0, 5 loops, 1394 model calls; quality gates that fail are reported as FAIL in the NOTE | 1.92995845 USD |
| 2026-10-28 | `diag-test1x1-1028-1531` | [diag-test1x1-1028-1531-NOTE.md](2026-10-28/diag-test1x1-1028-1531-NOTE.md) | Diagnostic paid loop: card opens within 10 s of its frame, every frame has an outcome, digit-phone hide within 5 s, loop not INCOMPLETE | All four diagnostic targets PASS (version 1 p50 3.687 s, max 4.616 s; 351 frames SETTLED; phone hide max 0.028 s); SKU and reference quality failures remain | 0.51845225 USD |
| 2026-10-28 | `e2e-1028-091344` | [e2e-1028-091344-NOTE.md](2026-10-28/e2e-1028-091344-NOTE.md) | Unpaid end-to-end loop on final main after PR #34 with stored stand-ins: runner, console, SSE reconnect, health | Runner rc 0, worker failures 0, SSE resumed without gap; image path NOT_RUN at 0 USD; not a live PASS | 0 USD |
| 2026-10-28 | `e2e-1028-1524` | [e2e-1028-1524-NOTE.md](2026-10-28/e2e-1028-1524-NOTE.md) | Offline runner plumbing check, one 330 s loop on `650fe39` with a blank model key | Runner exit 0, barriers SETTLED, runner_failures 0, 0 model_call rows | 0 USD |
| 2026-10-28 | baseline `650fe39` | [media-time-binding-NOTE.md](2026-10-28/media-time-binding-NOTE.md) | Acceptance baseline for binding orders to the item on screen at media time, before the implementation | 0 passed, 5 ordinary assertion failures on the baseline (red tests as intended) | 0 model calls |
| 2026-10-28 | baseline `650fe39` | [sku-ambiguity-NOTE.md](2026-10-28/sku-ambiguity-NOTE.md) | Acceptance baseline for SKU ambiguity and uncertain identity, on recorded input | 1 failed, 19 passed (the failure is the intended acceptance evidence) | 0 USD |
| 2026-10-28 | PR #45 at `0f08af4` | [slow-vision-integration-NOTE.md](2026-10-28/slow-vision-integration-NOTE.md) | Scheduling and shield regression with synthetic slow vision service times, serial failure reproduced on `bf620a4` | Compressed checks pass on PR #45; full-speed 1:1 rerun 5 passed, 2 xfailed in 364.50 s | 0 USD |
| 2026-10-28 | baseline `650fe39` | [written-phone-NOTE.md](2026-10-28/written-phone-NOTE.md) | Acceptance baseline for spelled-out phone numbers against the existing shield `scan()` | 39 failed, 514 passed on the baseline; misses all five written-number positives | 0 USD |
| 2026-10-29 | `e2e-main-1029-2140` / `-2140s` | [e2e-main-1029-2140-NOTE.md](2026-10-29/e2e-main-1029-2140-NOTE.md) | Unpaid end-to-end check of the C5 scenario on main `f2c2dba` (merge of PR #61): a dry run and a run against a stand-in app; both sessions unbound (legacy order flow) | Situations 3 and 4 FAIL on both runs (3: 16/28 dry, 16/26 stand-in; 4: 0/4); scripted interventions 22 (208.89 per hour) and 20 (199.59 per hour) | 0 USD |
| 2026-10-29 | `rescore-base-1029-194618` | [rescore-base-1029-194618-NOTE.md](2026-10-29/rescore-base-1029-194618-NOTE.md) | Unpaid replay of the stored `diag-test1x1-1028-1531` observations on main `a767f02`, the baseline before PR #61 | References right 8/28 (0.286); receipts with the right item 0/2; 19 scripted interventions (178.7 per hour) | 0 USD |
| 2026-10-29 | `rescore-main-1029-2128` | [rescore-main-1029-2128-NOTE.md](2026-10-29/rescore-main-1029-2128-NOTE.md) | The same replay on main `f2c2dba` (PR #61, media-time attachment binding) | Same numbers as the baseline (references 8/28); the session was unbound, so PR #61's flow did not run | 0 USD |
| 2026-10-29 | `rescore-bind-1029-2218` | [rescore-bind-1029-2218-NOTE.md](2026-10-29/rescore-bind-1029-2218-NOTE.md) | The same replay with the session bound to the attachment flow | 30 of 30 attachments expired, 0 `ready`; references 0/28, which the NOTE reads as missing worker frame reads, not as a measurement of the reference logic | 0 USD |
| 2026-10-29 | `rescore-paid-1029-2333` | [rescore-paid-1029-2333-NOTE.md](2026-10-29/rescore-paid-1029-2333-NOTE.md) | Paid bound rescore, stage 1 (120 s cap), branch revision `4b7e13f` | Runner rc 143: the watcher stopped it (10 attachments, all expired, 0 `covered=True`); stage 2 not run | 0.12486890 USD (77 calls) |
| 2026-10-29 | plan `rescore-paid-1030` | [rescore-paid-1030-PLAN.md](2026-10-29/rescore-paid-1030-PLAN.md) | Plan and exact commands for the paid bound rescore, derived from revision `d8c7f5e` | A plan, not a result; it was run on 30 October (next rows) | 0 USD |
| 2026-10-30 | `e2e-main-1030-1852` / `-1852s` | [e2e-main-1030-1852-NOTE.md](2026-10-30/e2e-main-1030-1852-NOTE.md) | Repeat of the 29 October unpaid check on main `92ecf12` (merge of PR #66) | Situations 3 and 4 FAIL on both runs, with the same counts as on 29 October; `make check` 1473 passed | 0 USD |
| 2026-10-30 | `rescore-paid-1030-2243` / `-2248` | [rescore-paid-1030-NOTE.md](2026-10-30/rescore-paid-1030-NOTE.md) | Paid bound rescore, stages 1 (120 s) and 2 (350 s), on main `062dade`, after the worker's frame lane was repaired (#65) | Every frame read; 7 of 10 and 23 of 30 order comments reached `ready`; situation 3 at 5/9 (0.5556) and 10/28 (0.3571) against 0.9 | 0.26572490 USD (174 calls) and 0.75456405 USD (491 calls) |
| 2026-10-31 | `rescore-paid-1031-0144` | [rescore-paid-1031-NOTE.md](2026-10-31/rescore-paid-1031-NOTE.md) | First stage-1 attempt on `4c53e72`, a head of PR #74 that is not main | Lost when the host slept; loop INCOMPLETE; not scored | 0.03792415 USD (34 calls) |
| 2026-10-31 | `rescore-paid-1031-0737` / `-0742` | [rescore-paid-1031-NOTE.md](2026-10-31/rescore-paid-1031-NOTE.md) | Paid bound rescore, stages 1 and 2, on `4c53e72` (PR #74 head, not main); does not describe the final build | Stage 1 PASS 4 / FAIL 4; stage 2 PASS 7 / FAIL 3, situation 3 at 0.5 on n=28 | 0.25752100 USD (172 calls) and 0.80973035 USD (499 calls) |
| 2026-10-31 | `e2e-A-1031-0755` / `-0755s` | [rescore-paid-1031-NOTE.md](2026-10-31/rescore-paid-1031-NOTE.md) | The unpaid end-to-end check repeated on `4c53e72` | Every step rc 0; 0 `model_call` rows; `make check` 1489 passed with `data/local/test1` present | 0 USD |
| 2026-10-31 | `rescore-paid-1031-1615` | [rescore-paid-1031-NOTE.md](2026-10-31/rescore-paid-1031-NOTE.md) (section "Stage 2 again, on main") | One 350 s paid stage on main `bfc7611` (merge of PR #75), 100 comments of `stream-1.jsonl` | Scorer PASS 7 / FAIL 3 (3, 4, 5); references 15 of 28 right, 0 on a wrong card, 13 with no card; 9 interventions (84.95 per hour); receipt with image 0 of 1 | 0.79893985 USD (499 calls); see the ledger note below |
| 2026-11-01 | catalog kind component check | [catalog-kind-component-NOTE.md](2026-11-01/catalog-kind-component-NOTE.md) | Explicit catalog kinds used only for `other` items (D-035), offline, base `e576b4a` | Focused tests 12 failed / 1 passed on the base, 44 passed after; no gain in completed orders or C5 shown | 0 USD (no model call) |
| 2026-11-01 | product identity | [identity-1101-NOTE.md](2026-11-01/identity-1101-NOTE.md) | How many of the 13 order comments without a card in `rescore-paid-1031-1615` the card brain rescues without naming a wrong item (offline, brain level) | 1 rescued (`c5-1-031`), 12 still blocked, 0 on a wrong item; a paid run with live vision not measured | 0 USD |
| 2026-11-01 | manual baseline `founder-1` | [manual-founder-1-NOTE.md](2026-11-01/manual-founder-1-NOTE.md) | One person handling `stream-1.jsonl` by hand at normal speed with the manual-baseline kit (PR #79) | Of 28 order comments naming an item: the person 2 right, 3 wrong, 23 with no action; the assistant session `rescore-paid-1031-1615` 15 right, 0 wrong, 13 with no card; one person, one run, one stream | 0 USD |
| 2026-11-01 | model comparison | [model-compare-NOTE.md](2026-11-01/model-compare-NOTE.md) | Product identification on one frozen set of 70 frames: two Spark efforts and Wonder under three schemas (D-036) | On 50 clear frames: schema A 22 correct with both Spark efforts, B0 31–34, B 34–35; no wrong pick on the 6 no-product and 14 boundary frames | 3.7242853 USD (560 calls; cap 5.00 USD) |
| 2026-11-01 | receipt version lock | [receipt-version-lock.md](2026-11-01/receipt-version-lock.md) | The receipt keeps the card version the customer confirmed (synthetic data, no key) | Tests 7 failed / 5 passed before the change, 12 passed after; receipts with an image in the lock session 1/3 before, 1/2 after | 0 USD |
| 2026-11-02 | offline frame replay | [offline-frame-replay-NOTE.md](2026-11-02/offline-frame-replay-NOTE.md) | Stored frame completion in the unpaid rehearsal (offline stand-in only) | Reproduction 2 failed / 1 passed before the change; focused tests 77 passed after; component evidence, not a rerun | 0 USD (no model call) |
| 2026-11-02 | replay worker lifecycle | [replay-worker-lifecycle-NOTE.md](2026-11-02/replay-worker-lifecycle-NOTE.md) | Frame evidence kept across replay loops (one worker for all loops) | Six new cases fail on `38adaf4` and pass after; with the related tests 52 passed | 0 USD |
| 2026-11-02 | `c5-paid-1102-1028` (Run 1) | [c5-paid-1102-1028-NOTE.md](2026-11-02/c5-paid-1102-1028-NOTE.md) | Full-size paid C5 run, 5 loops, 500 comments, about 33 min, on main `02f6d73` | Intent 149 of 150 → right item 69 → order created 70 (chain 68) → confirmed 6 → receipt with right version and image 0 (version 6/6, image 0/6); 40 interventions (73.68 per hour against 10) | 4.242935 USD (2345 calls), not reconciled with billing |
| 2026-11-02 | `judge-paid-1102-1109` and two demo1 sessions (Run 2) | [judge1-demo1-paid-1102-NOTE.md](2026-11-02/judge1-demo1-paid-1102-NOTE.md) | demo1 replay and the judge1 video test flow with real models, on main `aa457d9` | FAIL: on judge1, of 7 order comments 2 as expected, 3 on a wrong card, 2 with no order or reply; no receipt in either specified session | 0.161111 USD for three sessions (0.035989, 0.087888, 0.037234) |
| 2026-11-02 | `e2e-main-1102-1123` | [e2e-main-1102-1123-NOTE.md](2026-11-02/e2e-main-1102-1123-NOTE.md) | Unpaid end-to-end check and TEST-C on main `aa457d9` | All three commands rc 0; situations 9 PASS, 2 FAIL, 1 TEST_ONLY, as on 30 October; TEST-C 5 passed at both speeds | 0 USD |
| 2026-11-02 | `c5-paid-1102-1426` | [c5-paid-1102-1426-NOTE.md](2026-11-02/c5-paid-1102-1426-NOTE.md) | Full-size paid C5 run, 5 loops, 500 comments, on main `4ed3041` (after PR #97) | 0 of 151 comment attachments ready (151 expired); 27 orders, 0 confirmed, 0 receipts; PR #102 reverted #97 | 4.07709765 USD (2565 calls) |
| 2026-11-02 | `judge-paid-1102-1644` | [judge1-demo1-paid-1102-NOTE.md](2026-11-02/judge1-demo1-paid-1102-NOTE.md) (section "Later run: judge-paid-1102-1644") | The judge1 video test flow with real models again, on main `46a0f0e` (after D-038 and PR #100) | FAIL: of 7 order comments 3 as expected, 1 on the right card in another state, 2 on a wrong card, 1 with no order; no receipt | 0.0747279 USD (52 calls) |
| 2026-11-02 | `judge-paid-1102-2006` | [judge1-demo1-paid-1102-NOTE.md](2026-11-02/judge1-demo1-paid-1102-NOTE.md) (section "Later run: judge-paid-1102-2006") | The judge1 video test flow with real models, on main `137ffca` (after D-040, PR #105) | FAIL: of 7 order comments 3 as expected, 1 on the right card in another state, 1 on a wrong card, 1 with a size question instead of asking which item, 1 with no order; no receipt | 0.0700995 USD (50 calls) |
| 2026-11-02 | `c5-paid-1102-1532` | [c5-paid-1102-1532-NOTE.md](2026-11-02/c5-paid-1102-1532-NOTE.md) | Full-size paid C5 run, 5 loops, 500 comments, on main `767f7fe` (after the revert in PR #102) | 137 of 151 comment attachments ready; right item 90 of 150; 93 orders, 6 confirmed; receipts 6, 0 with the card image | 3.97704250 USD (2484 calls) |
| 2026-11-02 | `c5-paid-1102-1709` | [c5-paid-1102-1709-NOTE.md](2026-11-02/c5-paid-1102-1709-NOTE.md) | Full-size paid C5 run, 5 loops, 500 comments, on main `31e7d8a` (after PRs #100 and #103) | Receipts 9, 9 with the card image; 9 confirmed; but 97 of 151 comment attachments ready (137 in c5-paid-1102-1532), right item 72 of 150, 72 orders | 4.25326475 USD (2536 calls) |
| 2026-11-03 | `judge-paid-1103-0019` (and `judge-paid-1102-2131`, recorded with it) | [judge1-demo1-paid-1102-NOTE.md](2026-11-02/judge1-demo1-paid-1102-NOTE.md) (sections "Later run: judge-paid-1102-2131" and "Later run: judge-paid-1103-0019") | The judge1 video test flow with real models on main `2a3ba2e` (after PR #108, D-042), compared with the run on `1a886f6` | FAIL: of 7 order comments 5 as expected and 2 on the right card in another state (right card 7 of 7; 6 of 7 in 2131, cause not established, n=1 each); only one of four items priced, no card image, no reply to two questions, no receipt | 0.06761595 USD (46 calls); 2131: 0.06632745 USD (44 calls) |
| 2026-11-03 | results recount and clean clone | [results-1103-NOTE.md](2026-11-03/results-1103-NOTE.md) | Ledger `SELECT` and data-file recounts behind `docs/RESULTS.md`, and one timed clean clone of mode 1 on main `2a3ba2e` | Ledger and file recounts equal the figures in the run notes and D-042; in `c5-paid-1102-1709` 2 of the 9 confirmed orders followed a plain-text reply the script expects to end in NEEDS_SELLER; clean clone to the judge1 console page 77 s, n = 1, with the PostgreSQL image and Python 3.12 already on the machine | 0 USD (no model call) |
| 2026-10-30 | `page-test-1030` | [facebook-readonly-1030-NOTE.md](2026-11-02/facebook-readonly-1030-NOTE.md) | Read-only Facebook comment source, one poll, on an already-ended live video of the author's Page, at `e18c5f9` (merge of PR #69) | rc 0; 1 comment, 0 cards, 0 orders; not run on a live broadcast in progress | 0 USD (model key empty) |

The Facebook row is dated by its run (30 October); its NOTE was written on 2 November.

## Ledger note: `rescore-paid-1031-1615`

The run cost 0.79893985 USD by the event ledger: 499 `model_call` events, the wrapper's `SUM` query and the sum of
`cost_usd` over the committed `2026-10-31/rescore-paid-1031-1615-model-calls.jsonl` (499 rows, 0.79893985 when
recomputed on 2 November). The value 0.273011 USD in its wrapper log (`rescore-paid-1031-1615-wrapper.log`, lines 5–6:
`head_seq` 462, `cost before` `today_usd` 0.273011) is the balance of that database at the start of the run, not
the run's spend. A read-only count on 2 November found no `model_call` row for this session in the current
`keviq_paid_test` database: the run wrote to a different database instance with the same name (its wrapper started
at `head_seq` 462 and 0.273011 USD); the current database still holds the 31 October runs, so the committed JSONL is
the record.

## Committed logs and JSONL files, 29 October to 2 November

Sizes and sha256 of every `.log` and `.jsonl` file committed under these folders, computed on 2 November at
`e5328cc` with `wc -c` and `shasum -a 256`. Paths are relative to `docs/evidence/`. Media, gold, raw comment
exports and the run files listed in the 2 November NOTEs stay outside Git and are referenced there by sha256 only.

| File | Bytes | sha256 |
|---|---|---|
| `2026-10-29/e2e-main-1029-2140-comments.jsonl` | 36183 | `20b04fce1a46eb3f9d6bf68fbf1c31b9720f036a5a3e5ec76b2ad6009fc2da86` |
| `2026-10-29/e2e-main-1029-2140-testc-compressed.log` | 2718 | `f85d78c49a40fa48af866c9208dab8b2dec60a5b2a4574687a177f2ddb6cc58d` |
| `2026-10-29/e2e-main-1029-2140-testc-full-speed.log` | 2725 | `f9f6197c968daf7a2923ef5f026232ad387be7cc2b50cb6d1aa667c54d518120` |
| `2026-10-29/e2e-main-1029-2140s-comments.jsonl` | 33296 | `0e95ec18d21cf8f1110b1f5ed4d86af45c11cf65865c82a8733f784f9a66bdab` |
| `2026-10-29/e2e-main-1029-2140s-runner.log` | 595 | `9fac343921c4828979e90a3ce89a36b60df43cd33a71f5d78ba407b960bedb26` |
| `2026-10-29/e2e-main-1029-2140s-sse-check.log` | 2401 | `c9e73916711a393a6472d93510f6075ac25958b7d6ac897731b63884b0a70e53` |
| `2026-10-29/e2e-main-1029-2140s-sse-mid.log` | 190 | `4c76380517706aa6fe0c5c78971ac1cb93205507734c4c1fd0be8ec77a65df19` |
| `2026-10-29/rescore-base-1029-194618-comments.jsonl` | 35991 | `190cddb1a0c9b1a7fd6764ca773426b50e0262890d6e15593c9393ef28e4436b` |
| `2026-10-29/rescore-bind-1029-2218-comments.jsonl` | 34047 | `14e361e482433e11c13be7468ef0e55f96fcd448f4085f0deb610c56e25be6d8` |
| `2026-10-29/rescore-bind-1029-2218-media-extract.log` | 995 | `aabf1c21ac76b9c5959efe2f7c939ab52cdc57ef2ad7ff7bdbaba69ab86637de` |
| `2026-10-29/rescore-bind-1029-2218-runner.log` | 610 | `af85bee5a20b5f95377f2820e80f8b52dbce62fc00db8eaae06e7e6f01847bc8` |
| `2026-10-29/rescore-bind-1029-2218-worker-extract.log` | 2316 | `41080c2d3efdc85c397629e69357afe1e78324e192bfa5116aba04327a01d526` |
| `2026-10-29/rescore-main-1029-2128-comments.jsonl` | 35991 | `5519708fa5850e6cc573873adeb0c7c3d47458fc5822648d900711a6d23ab96f` |
| `2026-10-29/rescore-paid-1029-2333-app.log` | 90726 | `dcb7b953e099f782889ad68f3832f24edb9bfa5a72232320a45089b83c654047` |
| `2026-10-29/rescore-paid-1029-2333-image-guard.log` | 725 | `34ef84cd7cb04087263436ad3498658e316462a0fba9c112aa160f53483ddd00` |
| `2026-10-29/rescore-paid-1029-2333-model-calls.jsonl` | 47223 | `443c7f80132cf98a36a8f46136b1123730047491866adcd4908fb564efa4cb75` |
| `2026-10-29/rescore-paid-1029-2333-runner.log` | 68 | `f88f44ccc4b473ea351c722193ece0646eadc3e218fe738486b582f15468c95c` |
| `2026-10-29/rescore-paid-1029-2333-watch.log` | 2160 | `b2bc2bfe58b3058a9a2f5c5960b3ad327231a1a8e01d94ac6bc666c1746aa8e6` |
| `2026-10-29/rescore-paid-1029-2333-wrapper.log` | 1271 | `fbd10b120f4c46a9d3c1abeadc4b36c1b91562d9d73dac573fe3f16eaf79ec92` |
| `2026-10-30/e2e-main-1030-1852-comments.jsonl` | 36183 | `198c3114e6494e696eb238cb2e76f0bdf7fdc00e9e0bf53c11b9a00e9a6954a0` |
| `2026-10-30/e2e-main-1030-1852-dry-runner.log` | 521 | `ace74c6f7d6ef1097459347e67bcc38aa5c72b7b64c8e75c69b0d30a95a8b944` |
| `2026-10-30/e2e-main-1030-1852-make-check.log` | 2677 | `fc6f05c9ed12b0dbb15ca055ef97409d3d6fbfdbfb487ba308e87d639276cdeb` |
| `2026-10-30/e2e-main-1030-1852-testc-compressed.log` | 2717 | `b63b29c042f06f62d75f47f0ec7904f4b5a84509cbfe73c0cbae0c3af3ab959b` |
| `2026-10-30/e2e-main-1030-1852-testc-full-speed.log` | 2715 | `458b4a7455f61a0823e09d736fef207a7342554a0804fb4555585ed5e0bf85af` |
| `2026-10-30/e2e-main-1030-1852s-comments.jsonl` | 33296 | `fcbb809da102bed3ca683612be44b1d94b1136ba54a0f573d2a02a8f7cb358a5` |
| `2026-10-30/e2e-main-1030-1852s-runner.log` | 595 | `697ed3a17e0307448435cf61dd3f099431e216d0ae1ecae9fe7e0d1f9ad2d027` |
| `2026-10-30/e2e-main-1030-1852s-sse-check.log` | 3297 | `fa1465eeb80497f3a0affaa9d115342b54fcdc87ec1e9a3acfe254ee10111898` |
| `2026-10-30/e2e-main-1030-1852s-sse-mid.log` | 244 | `81e2a37db2d5712d7106a9e3f8e2a69b11b92fdf7c4701d5b8eb360f55749604` |
| `2026-10-30/rescore-paid-1030-2243-app.log` | 143794 | `094f37086922790af2a266a352023ef35c4ace761ffe113f2707dbc8f740e82e` |
| `2026-10-30/rescore-paid-1030-2243-comments.jsonl` | 12004 | `b22db9cefbf784e377ce180b2a4c39ed103600af28449aa6fe8dd0d77d583bb3` |
| `2026-10-30/rescore-paid-1030-2243-image-guard.log` | 725 | `f35ef0aec7b5ba3fd989c2bbe648f7fb80cdd5fc01c5429bd1c19c4b84997916` |
| `2026-10-30/rescore-paid-1030-2243-loop1-media.log` | 20817 | `bfbca39deda9d0b91c54559ff145343c959ad8024ef50d4f9ae5d7662f18f29e` |
| `2026-10-30/rescore-paid-1030-2243-loop1-worker.log` | 285602 | `64793ee5f094cd7c986327064f4a220a930727fd5278a355ad02bef2e21823d2` |
| `2026-10-30/rescore-paid-1030-2243-model-calls.jsonl` | 106591 | `f7740f7dd784ff5cdbdbde14b8133842e53f493cf9b65c3ecdc2609510cfb17e` |
| `2026-10-30/rescore-paid-1030-2243-runner.log` | 678 | `c17a088890944b59fb6256f329a7378514e060f92f4a9c8034c3d377804f5dca` |
| `2026-10-30/rescore-paid-1030-2243-watch.log` | 1696 | `b9836f439183c747cb77a7bd9784e7729bc8e7dafa0d83105d10ecad8b4bb4c6` |
| `2026-10-30/rescore-paid-1030-2243-wrapper.log` | 1688 | `9baaa4f34b24fe215914aa5ff4629b8b8081d01c76194961faae715f97d6dbfe` |
| `2026-10-30/rescore-paid-1030-2248-app.log` | 388874 | `7479f6f70103a6711c27faab56041b251392c5ea09aef3bc5ef6a5ca8effabd5` |
| `2026-10-30/rescore-paid-1030-2248-comments.jsonl` | 34535 | `fbd9a66c4dae388cc78b4098bb6a6e90ec3a54c57724d03f40a4bf6264520598` |
| `2026-10-30/rescore-paid-1030-2248-image-guard.log` | 723 | `9a411e4232007eeea7b3cb42cd5d704a0a35f7bbf3f8394a2eed933b73362883` |
| `2026-10-30/rescore-paid-1030-2248-loop1-media.log` | 56574 | `ea138d6041322d4f8d89a81058e0c55c772fa4d1edc55c2712d4918197e7aa82` |
| `2026-10-30/rescore-paid-1030-2248-loop1-worker.log` | 781220 | `176c6fb23960eb53da4e290bb4beb4db6e0f12e98baf4c1d734dd4578327df12` |
| `2026-10-30/rescore-paid-1030-2248-model-calls.jsonl` | 301322 | `45c935a954df380cd9627b22eee690763c99df1125b858b029580ae968db88b8` |
| `2026-10-30/rescore-paid-1030-2248-runner.log` | 688 | `5c8160c0d748f0db9df0f874ef824b742a3ff34b20a895039db418b9fb364249` |
| `2026-10-30/rescore-paid-1030-2248-watch.log` | 3924 | `3a9342ae9b2822395df7d91816bd68cd75f47a5cb9260fd7a48060b6e90e75c1` |
| `2026-10-30/rescore-paid-1030-2248-wrapper.log` | 1704 | `0b2b40eb55cbada1ce07d4063d225fae29c98e1f7e4aa10c78e1f7c8b6e0b710` |
| `2026-10-31/e2e-A-1031-0755-comments.jsonl` | 36183 | `eeeed03698f8b81a0b4c6b2c2b5da2d2cdafef85dde5acff4b812529fe95493d` |
| `2026-10-31/e2e-A-1031-0755-dry-runner.log` | 524 | `29eb931eb40767cea25605fa80582b930d367ccb931fd6c553fa5f4906d1ff67` |
| `2026-10-31/e2e-A-1031-0755-make-check.log` | 2677 | `230927bdce6d37400b1e0d132e0a28129e3b2bc38e26c76fca1b9f34fcbe51d8` |
| `2026-10-31/e2e-A-1031-0755-testc-compressed.log` | 2721 | `39eb2e5e9043c0a20784b7ad73fee7a8759d7cfec6363d0fa91319a06622749f` |
| `2026-10-31/e2e-A-1031-0755-testc-full-speed.log` | 2719 | `22ec56a6d13be550d8c69d12fad61994c2231dfe2c97819119201ab92e6746fb` |
| `2026-10-31/e2e-A-1031-0755s-comments.jsonl` | 33296 | `83b2bf737119efdab5913ad2e4a0a44ccdf0b6367bb6288608e3e10dd284e947` |
| `2026-10-31/e2e-A-1031-0755s-runner.log` | 598 | `03dbb95c3da3b2ac7b79bad2e41d744d5162144c1276290a5ef988c8241454a0` |
| `2026-10-31/e2e-A-1031-0755s-sse-check.log` | 3279 | `45cc13b1766d349cc61f4f7c2307b834cb1ba369f6f5ce2c49c2ae864d154297` |
| `2026-10-31/e2e-A-1031-0755s-sse-mid.log` | 244 | `0edde3540f2eb4ee02a864203d8134796e7277c3d385afbbef67d30eae8be924` |
| `2026-10-31/rescore-paid-1031-0144-runner.log` | 684 | `205f9ee60f73256af7305b6e43a67a4b30036e5765d8b4a5fda5605ab1839178` |
| `2026-10-31/rescore-paid-1031-0144-watch.log` | 3242 | `5605999b595a44442aba26add25c114b0757246a80cf59e3a88803f1dd2fc89f` |
| `2026-10-31/rescore-paid-1031-0144-wrapper.log` | 1523 | `a9be5695ad46c38489fcb0685e97de44a11ad891a666dceb41f5dc9ab1267cb0` |
| `2026-10-31/rescore-paid-1031-0737-app.log` | 144026 | `ab4dfea986de6c7a486e63090c746b2422c8592a256be2de829ab6d922b5475d` |
| `2026-10-31/rescore-paid-1031-0737-comments.jsonl` | 12163 | `a6ae6cf0fac50686e5a3101d26031ec12708870b9d64be3b100d5fe2e3aac763` |
| `2026-10-31/rescore-paid-1031-0737-image-guard.log` | 725 | `9132d7c9862aae9009ec981fc809c1a166c69d3c89ef2cd72e38ec88f3677d44` |
| `2026-10-31/rescore-paid-1031-0737-loop1-media.log` | 20543 | `b803baab2c62b5a4fa34f0c32d9b5e8e8c75c89f4b025242c14ceb6ee65167cb` |
| `2026-10-31/rescore-paid-1031-0737-loop1-worker.log` | 280166 | `209e17945258f6f7eca30d0efec62130fdace7f5cc724b380db385daa78cc8cc` |
| `2026-10-31/rescore-paid-1031-0737-model-calls.jsonl` | 105523 | `0eee897fdfd7ce387177bfd57ab89acd598a31c4e8306ce177d60d1340794035` |
| `2026-10-31/rescore-paid-1031-0737-runner.log` | 678 | `f316d68dcf1d4aecac8962d40fbbff9a98571da27e561c7e19803058d5cdb6fd` |
| `2026-10-31/rescore-paid-1031-0737-watch.log` | 1710 | `340c4d16851cd651331bd4fd6c5c637cc97f7fb883d3152779e6c4034355b79d` |
| `2026-10-31/rescore-paid-1031-0737-wrapper.log` | 1694 | `98329547ab87e9751f7b3cb792c5f1ab32c26ab584bc76e72a84d139f7f155ab` |
| `2026-10-31/rescore-paid-1031-0742-app.log` | 394134 | `4ce07750346b57d16dc62787a7988f282839ccfe03ba6a2969b10ce4d5dbeb5c` |
| `2026-10-31/rescore-paid-1031-0742-comments.jsonl` | 35100 | `bf6444620680629404614a53f0894c14df3164fbeb5f32d3a3390061fb6d5620` |
| `2026-10-31/rescore-paid-1031-0742-image-guard.log` | 723 | `005526b23c3c35bf32cd32e57449628f104b5ecc43aafbf31b7c6c057965801b` |
| `2026-10-31/rescore-paid-1031-0742-loop1-media.log` | 57259 | `dd1cd4cfc82fe64204dc323840029ee05c03d9e8c3f49e154a7e1b11f84ce36b` |
| `2026-10-31/rescore-paid-1031-0742-loop1-worker.log` | 775057 | `663f91c82ff05b9401e416b2ba83b6aad60031601f71d347f5b16b692fd05891` |
| `2026-10-31/rescore-paid-1031-0742-model-calls.jsonl` | 306334 | `229c97bb3f4e08469795f28f984ea2cd5a91f7edb0b308eeb5241efc4ed85329` |
| `2026-10-31/rescore-paid-1031-0742-runner.log` | 688 | `c4e4a81b2529984193ee5ba47bca4507179f1d3aae245947eebba414687543c6` |
| `2026-10-31/rescore-paid-1031-0742-watch.log` | 3924 | `b4c06ad304b025d6bb47c5a4b6c97c6d679b04ec58cbe6cc78c1a51d69b89d5d` |
| `2026-10-31/rescore-paid-1031-0742-wrapper.log` | 1704 | `048bccc154d0b3805364cd383a4e68d12ea60cdfbe4655c148ee5c0eb689ab2b` |
| `2026-10-31/rescore-paid-1031-1615-app.log` | 391025 | `f2e8710d0f93402d789bfe39fa7df54f7007442fe8819fb85eefb52baf078233` |
| `2026-10-31/rescore-paid-1031-1615-comments.jsonl` | 35110 | `8936c9245993b810d549d86fa0ee2e0dc32c988499b43bd8ad734d324f0c82b3` |
| `2026-10-31/rescore-paid-1031-1615-image-guard.log` | 723 | `b029d195f9ab970a23eb2579a136d7b5f187c5e339741a4f011e28b64dc90132` |
| `2026-10-31/rescore-paid-1031-1615-loop1-media.log` | 57259 | `a40d4d1ab09c9177756766802c9016b27cb7b9044ef7ac1e2ea7630393973497` |
| `2026-10-31/rescore-paid-1031-1615-loop1-worker.log` | 784691 | `74fb0f86bd67397b3df6dbcd06c3377d4362ea38f3cfca752bccfd672ecd9ce6` |
| `2026-10-31/rescore-paid-1031-1615-model-calls.jsonl` | 306109 | `3e8eb4abedde3b274fc0fe0fa6d029b62e2b549cf0c45b45d7279d7bd8a0ba69` |
| `2026-10-31/rescore-paid-1031-1615-runner.log` | 688 | `e5f0060e305d55ee5d281df16ea97354c10905ea5b5438e0f419971bbd439743` |
| `2026-10-31/rescore-paid-1031-1615-watch.log` | 3921 | `4696004b9dc8ffaf82fd2d2d1d6be619c8853e44d220f69e276b70f0ff3b9846` |
| `2026-10-31/rescore-paid-1031-1615-wrapper.log` | 1703 | `d4065ff3aa85ccaedb83a854fa269fdb7bdc753c26ff4008624a4fffc168182c` |
| `2026-11-01/identity-1101-paid-0742-events.jsonl` | 235858 | `5255380e49fa29ca7faace83eb20247e8b853bdc98a6d562758b6d41ad8e0214` |
| `2026-11-01/identity-1101-paid-2248-events.jsonl` | 277244 | `08c00dc36ef69139c7e5556c776f85fa95e0bb951b310923a6035a580ae79679` |
| `2026-11-01/identity-after-1101-2008-comments.jsonl` | 34047 | `38d4877de2bd78c8d116eda22e6c9d905e1c729f4bc6f362901163bb65cbfc88` |
| `2026-11-01/identity-after-1101-2008-events.jsonl` | 259311 | `c6cbe241de7fe98ce2d2dfae4c23cde95ef5eb8e014d413ccf52eebf1f00b397` |
| `2026-11-01/identity-after-1101-2008-wrapper.log` | 1464 | `b277db320edf308228521117e34dd33579b5a47378500093613b081052768fce` |
| `2026-11-01/identity-before-1101-1900-comments.jsonl` | 34047 | `c0a6aeccecb09efe7a051de1a1ecfd43b92aca1ce11eaeedb0c8cdf79df951e6` |
| `2026-11-01/identity-before-1101-1900-events.jsonl` | 271022 | `680346df16013fe532733d67785ad6d33f8d3a7b19ea7444e3784b9e073489e9` |
| `2026-11-01/identity-before-1101-1900-wrapper.log` | 1469 | `bada56f87f41b100dbcf349972e1d24449f274040ab6f57f0f80ccabed463fc2` |
| `2026-11-01/manual-founder-1-practice/founder-1-practice/actions.jsonl` | 414 | `d68a4920045b1bd532ed0e49231a3b222496e3a960f82f565740d6ae2a9f92c1` |
| `2026-11-01/manual-founder-1-practice/founder-1b-practice/actions.jsonl` | 432 | `ca99817632f673ad32c70475139b5e9c46c0cc3640253e4089580499b763d520` |
| `2026-11-01/manual-founder-1-practice/founder-1c-practice/actions.jsonl` | 577 | `4811b54ae3d61d40c08e7e29fe65dbc7de8f052eda1329fd2cb2d2b49eb4533f` |
| `2026-11-01/manual-founder-1/actions.jsonl` | 2913 | `6bf671aea78811bd95eba96c2e1f1edcf2b3250376ebde5768fd432c2e686723` |
| `2026-11-01/model-compare-calls.jsonl` | 286264 | `ae4ad8d8dc45250b86efaf8d21c2c99e66959dea89ccb36f968e435948a10ec0` |
| `2026-11-01/model-compare-model-calls.jsonl` | 179497 | `8fe4455c286687d998233546bcdf39d8159a1f2d01151b547925c73a0cfefab9` |
| `2026-11-01/receipt-version-lock-session.jsonl` | 3905 | `5e081d382291f3868bf3f57f1d2db2485cdac748bf1c4139462dda192a8a31fb` |
