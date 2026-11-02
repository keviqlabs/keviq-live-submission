# TEST-C and E2E at 0 USD on main aa457d9 — e2e-main-1102-1123 — 2 November 2026

Cleaned copy of the run note kept outside the repository (source sha256
`dd56e3b3de62f4114ed0ce94496df3592d89c31f42117f5c3d499afb52a5413e`): local paths are removed and times are in ICT. The
numbers are unchanged. One "Later" line was added at the end of the outcome.

## Outcome
- Code: origin/main `aa457d98785a51320aa80a62c25744941b946afc`, tree `6b7da8ff3e1ba724fffcf62f8d1f0a75ed17ec2e`, a clean clone outside the working copy, no local change.
- Database `keviq_test` through the suite lock (`tests.db_lock`). `MODEL_API_KEY` set to the empty string by the wrapper (`key_set=no`), `C5_OFFLINE` unset by the operator.
- **Model calls 0.** Ledger for the session: `0|0` (count | USD). Both TEST-C runs report `model_calls 0`.
- All three commands rc 0. Situations: 9 PASS, 2 FAIL, 1 TEST_ONLY — the same statuses and counts as the 30 October baseline (`docs/evidence/2026-10-30/e2e-main-1030-1852-NOTE.md`, main `92ecf12`).
- Four steps (proposal → card version → customer confirmation → receipt) hold for both orders, with the same structure as the baseline. Receipts carry no image, as in the baseline.
- Changed against the baseline: 12 orders on cards without a SKU now go NEEDS_SELLER "has no SKU" → seller approved → CONFIRM_SENT. On 30 October the same 12 orders went NEEDS_SELLER "qty > stock 0" → seller rejected → CANCELLED. The NEEDS_SELLER reason now drops the stock reason when another blocking reason exists (`orders/flow.py:477`). Details below. The test time of TEST-C is longer (178.44 s vs 77.37 s compressed; 505.17 s vs 415.24 s full speed); no cause was measured.
- Later: PR #95 (merged 2 November 2026, merge a642d8e) shows the rules' refusal next to other blocking reasons. Its dry run `e2e-v4-1102-1305` (rc 0, model calls 0) brought `order:over_stock->CANCELLED` back to 13 (PR #95 description).

## Commands and rc
Wrapper `testc_e2e.sh` (kept with the run files), output `testc-e2e-wrapper.out`:

| Step | Command (in the clone) | Result |
|---|---|---|
| dry E2E | `uv run python -m tests.db_lock -- python -m measure.c5 --dry --loops 1 --no-code-check --run e2e-main-1102-1123 --out <folder>/c5` | rc 0, 11:27:54; `loop 1: done, retired 11, waits {'many': 1}, failures 0`; `(0 rows)` model_call |
| TEST-C compressed | `uv run python -m tests.db_lock -- env SLOW_VISION_REPORT=<folder>/testc-compressed.jsonl uv run python -m pytest -q -s tests/test_slow_vision_integration.py --tb=short` | rc 0, `5 passed in 178.44s (0:02:58)` |
| TEST-C full speed | same with `SLOW_VISION_SPEED=1`, report `testc-full-speed.jsonl` | rc 0, `5 passed in 505.17s (0:08:25)` |
| ledger | `SELECT count(*), COALESCE(SUM((body->>'cost_usd')::numeric),0) FROM events WHERE type='model_call' AND session_id='e2e-main-1102-1123'` | `0|0` |

Start 11:21:28 ICT, end 11:39:27 ICT.

## Dry run events
466 events, seq 1130588..1131053: comment_labeled 110, comment 100, order 77, speech 64, message_out 54, card 42, product_seen 12, message_in 4, price_proposal 3. Same count and distribution as the baseline (466).

## Situations (the twelve of the scorer)

| # | 30 October `e2e-main-1030-1852` | 2 November `e2e-main-1102-1123` | Changed |
|---|---|---|---|
| 1 | PASS 10/10 | PASS 10/10 | no |
| 2 | PASS 3/3 | PASS 3/3 | no |
| 3 | FAIL 16/28 (0.571; previous_card 4/4) | FAIL 16/28 (0.571; previous_card 4/4) | no |
| 4 | FAIL 0/4 | FAIL 0/4 | no |
| 5 | PASS 2/2 | PASS 2/2 | no |
| 6 | PASS 1/1 | PASS 1/1 | no |
| 7 | PASS 1/1 | PASS 1/1 | no |
| 8 | PASS 1/1 | PASS 1/1 | no |
| 9 | PASS 4/4 | PASS 4/4 | no |
| 10 | PASS 1/1 | PASS 1/1 | no |
| 11 | TEST_ONLY | TEST_ONLY | no |
| 12 | PASS 2/2 | PASS 2/2 | no |

Source: `c5/e2e-main-1102-1123-scenarios.json` and the baseline `e2e-main-1030-1852-scenarios.json`.

## Score differences against the baseline
From a key-by-key diff of `score` in `c5/e2e-main-1102-1123.json` and the baseline run file:

| Measure | 30 October | 2 November |
|---|---|---|
| Interventions per hour | 208.91 (22) | 208.90 (22) |
| by cause: `order:over_stock->CANCELLED` | 13 | 1 |
| by cause: `order:other->CONFIRM_SENT` | — | 12 |
| needs_seller reasons `qty # > stock #` | 13 | 1 |
| needs_seller reasons `card-#-# v# has no SKU` | — | 12 |
| Final order states (DB, last state per order) | CANCELLED 14, CONFIRMED 2, CONFIRM_SENT 11 | CANCELLED 2, CONFIRMED 2, CONFIRM_SENT 23 |
| Receipts at the confirmed version | 2/2 | 2/2 (`receipts.version_and_image` 0: no image) |
| Phone visible max | 0.064 s | 0.066 s |
| Proposal handling median | 4075 ms | 4069 ms |
| New blocks in the scorer output | — | `funnel` (tiers intent 30, right_item 16, order 27, confirmed 2, receipt 0), `shop` (actions 22: order:200 19, proposal:200 3; failed 0) |

Latency percentiles moved by a few ms each way (for example `order.receive.p50` 46 → 54, `price_proposal.receive.p50` 51 → 15); not treated as a change.

### What the events show about the 12 orders
- The 12 orders are o-c5-1-032, 035, 041, 043, 045 (card-1-02, a white sandal card), 074, 078 (card-1-06), 082 (card-1-08), 085, 091 (card-1-09), 095, 098 (card-1-10). Each of these cards has `sku` null in the session's card events.
- 2 November: each goes NEEDS_SELLER with reason `card-1-NN vN has no SKU`, then `seller approved: unit price … = card price (was: … has no SKU)` → CONFIRM_SENT.
- 30 October: the same 12 order ids went NEEDS_SELLER `qty 1 > stock 0` (o-c5-1-098: `qty 3 > stock 0`), then `seller rejected` → CANCELLED.
- The reason text is produced at `orders/flow.py:447-448` (`if not card.get("sku"): blocking = [..., f"{card_id} v{card['version']} has no SKU"]`), added in commit 15c10ee "Lock an order to a later card version that only adds the image or name", which is after 92ecf12.
- **Cause (from the code, reproduced offline at 0 USD):** the rules still refuse these drafts. `orders/rules.py:87-88` gives `qty 1 > stock 0`, and card stock is 0 with variants [] in every version of these cards on both days. But `orders/flow.py:477` `why = blocking or [decision.reason]` keeps only the blocking list when it is not empty. Since 15c10ee puts "has no SKU" in `blocking`, the stock reason is dropped from the NEEDS_SELLER reason. The simulated seller rejects only when the reason contains "> stock" (`measure/c5_shop.py:8`, `:28`, `:269`), so it now approves. Line 477 dates from 7a071ad (27 October, before 92ecf12); the new blocking entry is what changed.
- Offline check `nosku_check.py` (run in the clone with `PYTHONPATH=. uv run python …`, rc 0, output `nosku-check.out`):
  - `rules: False 'qty 1 > stock 0'`
  - reason now `['card-1-02 v2 has no SKU'] -> shop cause other`
  - without blocking `-> over_stock`
  - both reasons `-> over_stock`
- Effect: on 2 November these 12 orders reached CONFIRM_SENT with qty above card stock 0. The seller had approved a reason that did not mention stock. Seller approval before CONFIRM_SENT still holds, and none of the 12 reached CONFIRMED.
- The remaining over-stock order is o-c5-1-018 on card-1-00 (CANCELLED both days).

## TEST-C (`tests/test_slow_vision_integration.py`)

| | compressed 30 Oct | compressed 2 Nov | full speed 30 Oct | full speed 2 Nov |
|---|---|---|---|---|
| pytest | 5 passed in 77.37 s | 5 passed in 178.44 s | 5 passed in 415.24 s | 5 passed in 505.17 s |
| frames / comments | 351 / 100 | 351 / 100 | 351 / 100 | 351 / 100 |
| digit phone hidden | 3/3 | 3/3 | 3/3 | 3/3 |
| hide latency, logical ms (c5-1-002 / 007 / 041) | 200 / 360 / 280 | 200 / 200 / 240 | 20 / 22 / 21 | 13 / 11 / 26 |
| model_calls | 0 | 0 | 0 | 0 |
| vision done / skipped (failed) | 146 / 205 (0) | 155 / 196 (0) | 174 / 177 (0) | 174 / 177 (0) |
| barrier | SETTLED, errors 0 | SETTLED, errors 0 | SETTLED, errors 0 | SETTLED, errors 0 |
| wall (`wall_elapsed_s`) | 19.2 s | 19.144 s | 354.2 s | 353.998 s |

`drain` and `stopped` cases on both speeds: early INCOMPLETE; drain final SETTLED; stopped final INCOMPLETE — as the test expects.

The longer pytest time is outside the measured window (`wall_elapsed_s` is the same); no cause was measured, none is claimed.

## Four steps, by sequence number
File `e2e-main-1102-1123-four-steps.txt`, written by `four_steps.py` (read-only SELECT on `keviq_test`). Header `events 466 orders 27 confirmed 2`, as in the baseline.

- `o-c5-1-016` (card-1-00, SP01): (1) price_proposal seq 1130636 (heard 220000, current 250000, card_version 4) → (2) card v5 220000 seq 1130642, status edited, edited_by seller, caused by the proposal → DRAFT 1130655, NEEDS_SELLER 1130656 (variant size 40 not on v5), CONFIRM_SENT 1130662 (seller approved, unit price = card price) → (3) message_in `confirm_yes` 1130683, CONFIRMED 1130684 (actor customer) → (4) receipt message_out 1130685 at card version 5; `version_exists True`, `price_matches_version True`, image False.
- `o-c5-1-029` (card-1-01, SP02): DRAFT 1130718, NEEDS_SELLER 1130719 (size 41 not on v3), CONFIRM_SENT 1130726 → message_in 1130742, CONFIRMED 1130743 → receipt 1130744 at v3 320000, version exists, price matches, image False.

Same steps, states, actors, prices and versions as the baseline (seq 495310…495418).

## Files (outside Git, sha256)
`SHA256SUMS.txt` (1425 bytes, sha256 `32a2488ee49a1c05457446e880be9f370c2f276cdd4a85adfad891db88ca6906`) lists every file below.

| File | Bytes | sha256 |
|---|---|---|
| `c5/e2e-main-1102-1123-comments.jsonl` | 36195 | `708563e69cdb69869362f6f6cea15f986aa5e64de51ce54ca9269d93ea5b2379` |
| `c5/e2e-main-1102-1123-scenarios.json` | 3610 | `83b57bc0581278c9b03c7e95f1af7475876dc5c429bc892266a29a3ae075fb1e` |
| `c5/e2e-main-1102-1123.json` | 51118 | `c2325a83aee564d7fab2dbe17bb3705e6a8f1d96058391c436eebf37c1ac354c` |
| `e2e-main-1102-1123-dry-runner.log` | 701 | `a999f6a49f2a39ec445dcecac9fa1370eea0dec973b3850f9f1f0d822a6da686` |
| `e2e-main-1102-1123-four-steps.txt` | 2060 | `08b59d865ed5ff2b795a4e09b2d39b098192f965c4787e4a6758c7a873ff82cc` |
| `e2e-main-1102-1123-ledger.txt` | 4 | `2ce220d8d046f85fd648a70bc8e4b330562a745e56a371ee36e2596a39f56a37` |
| `e2e-main-1102-1123-testc-compressed.log` | 2734 | `a91eb86b64538b1bca4ba5944c4c6eaa5fbd18c1e51190efb70da95297e3abdb` |
| `e2e-main-1102-1123-testc-full-speed.log` | 2732 | `ebb4f18c81fb29960c93b6799b44b83e6d81c965e66b1d609d9c874c80bc2490` |
| `four_steps.py` | 2781 | `326deb27f9f917c7f248f3e1416bad3fd627b13201c594d53f8341312de36205` |
| `nosku-check.out` | 247 | `b2c563bbf0dca38e8f6227bbda1ea51911d27e55c50eb7afe13ad7d108f3b2c2` |
| `nosku_check.py` | 692 | `6c96ef1521df3bfabb17e534b5cf0b3fffce95595065145e0330187c51be0d0c` |
| `testc-compressed.jsonl` | 17822 | `0174702a311f088d6ca0ce797e76b831130ea1363b0dc69fda1e85afebefd7f8` |
| `testc-e2e-wrapper.out` | 232 | `6d81690b124afc3adae059d1cbdba7c357b1c95baeea808309aa7564f9d4c562` |
| `testc-full-speed.jsonl` | 17763 | `d26b8da0c94900921da52b8003712d665f443871d9a3e12a44e93d277c8e7792` |
| `testc_e2e.sh` | 1705 | `e6ffab045c1078906922c20451631a026924129a2f20a2b515d2817152abe9a4` |
