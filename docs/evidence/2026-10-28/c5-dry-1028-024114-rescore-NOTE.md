# Scope of the C5 dry run c5-dry-1028-024114, rescored

What this is: one dry loop of the C5 runner (`python -m measure.c5 --mode dry`), at 0 USD, rescored with the
scorer in this PR. It is not the C5 measurement: the 5-loop live run with real vision and real speech has not run.
Every number below is a dry-run number and waits for the independent review of the definitions before use.

## Run

- Run and session id: `c5-dry-1028-024114`. Commit at run time: `9b863a3` (main `e51183b` plus the C5 data
  commits). The runner and scorer files were not yet committed at run time; this rescore uses the scorer in this
  PR, `measure/c5_score.py` sha256 `e4ac62f5947689ed0a2f12db75f617c0388efb451df2a7cfe74ed923d0f37fba`.
- Rescore: the same session's events read back from `keviq_test` through `measure.c5_dry.read_all` (the store's
  reader, which keeps `seq` and `ingested_ms`), the same streams, gold and catalog.
- Files: `c5-dry-1028-024114-rescore.json` (sha256 `ca7adb75fbb1273eb791635edee44c9135e9a724d2fabd31c91cdf2b073c4424`),
  `-rescore-scenarios.json` (`f5a417ff399470276b8f1e49ec3881f18dfebfb3211fde7eb7ab2d0ec6134cd9`),
  `-rescore-comments.jsonl` (`984c7390c13871b07fc35f770fe15430cda814270937846d4865cbd8e137b9e6`). They hold ids,
  labels, item codes and counts; no comment or speech text.
- Loop 1: t0 `1793130075915`, end `1793130460026`, settled, 9 cards retired at the end, 0 runner failures.

## Config

- Mode dry: the app and the worker in one process; vision from stored rows
  `vision-test1-6b-minimal-1027-1950.json` (6b, effort minimal, 1 fps); no card line; gold labels through the
  labels brain (GoldClient); speech from the test1 lines with the rule price reader (no model key).
- Catalog: `data/comments/c5/catalog.json`, sha256 `fd5e86ad67ada1c0d200386fedd642e688440a34970e795d65b99ebc267adc73`,
  generated from the test1 gold. Gold source (local, not in git): `data/local/test1/labels.json`, sha256
  `eeeb5a69d9eb790ce366892a3ae8523cc3f4ab699cc62b0636567a2669ac7e65`, unchanged. Windows:
  `data/comments/c5/windows.json`, sha256 `12a1ad920e860a37ddccc375c91f6537f2d7d44ff49c9e89871fb5a38d7c3d51`.
- Who read CATALOG_PATH: in dry mode there is one process; `measure/c5_dry.py:194` asserts that the card brain's
  catalog and the labels brain's catalog both equal the C5 catalog before the loop starts. Media was not used
  (stored vision rows). In live mode each worker and media process prints its own `C5PROC proof` line
  (`measure/c5_proc.py`: catalog path and sha256, items, labels catalog same, cap and warning, effort, git sha);
  the live run has not happened, so no such line exists yet.
- Cost limits: `MODEL_SESSION_CAP_USD=8`, `MODEL_SESSION_WARN_USD=6`; `measure/c5_dry.py:216` asserts that
  `/api/cost?session=` returns 8.0 / 6.0 before the loop.
- Shop: scripted, 4.0 s per action. Customers: 4 scripted Messenger replies. Tail 30 s after the last frame.
- Restart at the loop boundary: dry mode drops the worker state inside one process; it is not a process restart.

## Ledger

```sql
SELECT session_id, count(*) AS n, COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd
FROM events WHERE type = 'model_call' AND session_id = 'c5-dry-1028-024114' GROUP BY session_id;
```

Result: `(0 rows)`. 0 model calls, 0 USD.

## Per loop

| loop | comments | orders | confirmed | cancelled | NEEDS_SELLER | would-be | interventions | proposals | waits | missed | items with a card |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 100 | 26 | 2 | 9 | 19 | 14 | 28 | 8 | 1 | 0 | 9 / 10 |

## Measures (targets from the C5 plan)

- Order recall: 1.0 on the 30 gold order comments (core.c5).
- Reference correct: 12 / 28 = 0.43 (target 0.9 on at least 50; n is below 50 in one loop). Current card 10 / 24,
  previous card 2 / 4, no card 0. Definition: core.c3's (the card the comment's latest label drafted).
- Phone visible: max 1.019 s, n 4 (target at most 5 s).
- Receipt version: 2 / 2 at the confirmed card version. With image 0 / 2, so `receipts_ok` is 0: a dry run sends
  no image.
- Missed orders (no draft within 60 s): 0 / 28; 2 comments name no item.
- Interventions: total 28 in 6.32 minutes, 265.9 per hour (target at most 10). By cause, summing to the total:
  price typed 6, price proposal used 3, variant then confirm sent 9, over stock then cancelled 8, price changed
  then confirm sent 1, discount then confirm sent 1. Hard cases stay in the total. The earlier 80 per hour figure
  was a risk waiting for the runner; this is the runner's count on a dry loop of a dense scripted stream, and the
  gate is judged on the live run.
- NEEDS_SELLER: 19 (variant 9, over stock 8, price changed 1, discount 1). Would-be candidates (D-026: an order
  in NEEDS_SELLER whose referenced card version's price matches exactly one catalog item): 14. Not a gate number.
- Waits: one price wait for reason many candidates; the end-of-loop snapshot, the deduped log lines and the
  suffix agree (HQ419 method).
- Cards: 9 of 10 items got a card (SP03 none). SKU assigned on 5 cards (card-1-00 SP01, -01 SP02, -03 SP04,
  -04 SP05, -05 SP06); none on card-1-06, -08, -09, -10 (D-026: a SKU only when exactly one catalog item matches;
  D-026 lists the known limits).

### Price proposals (D-024)

| proposal | card | outcome | closed by | right card | wrong | handling ms |
|---|---|---|---|---|---|---|
| price_proposal_380eb3597cc94f9a9d9e | card-1-00 | fixed | card_545458faf4b045ed8bde | yes | yes | 4028 |
| price_proposal_3a3262ad4e08442e8266 | card-1-00 | replaced | card_a54b9408528442388df4 | yes | yes | – |
| price_proposal_291a4036adee4498b760 | card-1-01 | dropped | c5-dry-1028-024114.retire-card-1-01-v7 | no | yes | – |
| price_proposal_ea0b0747e2c5499dbca9 | card-1-03 | used | card_b54a45cd805e4187a85a | yes | yes | 4051 |
| price_proposal_ee9dcf44a5184c4cb11d | card-1-03 | replaced | card_b54a45cd805e4187a85a | yes | yes | – |
| price_proposal_2809b7f8e1f8415caf9f | card-1-09 | used | card_ce21b0d5ebab43478723 | yes | no | 4074 |
| price_proposal_ec1b806fd3c64f4aaa27 | card-1-09 | replaced | card_ce21b0d5ebab43478723 | yes | yes | – |
| price_proposal_b34a0e2fef644e6faf78 | card-1-00 | used | card_a54b9408528442388df4 | yes | yes | 4024 |

Right card 7, wrong card 1. Wrong proposals 7: fixed 1, dropped 1, replaced 3, used 2. Median handling 4039.5 ms
(n 4, used or fixed). Each replaced proposal heard a price 100 đ above a sibling proposal on the same card, and the
version that used the sibling closed it.

### Latency (ms)

| stage | step | n | p50 | p95 |
|---|---|---|---|---|
| comment_labeled | receive | 109 | 27 | 52 |
| comment_labeled | wait | 109 | 1001 | 1005 |
| comment_labeled | infer | 100 | 0 | 0 |
| comment_labeled | display | 109 | 4 | 13 |
| message_out | receive | 54 | 10 | 47 |
| message_out | wait | 54 | 11 | 25 |
| message_out | display | 54 | 8 | 18 |
| speech | wait / infer | 64 | 0 | 0 |
| speech | display | 64 | 9 | 13 |
| product_seen | infer | 12 | 1883 | 3128 |
| product_seen | display | 12 | 4 | 12 |
| card | receive | 30 | 18 | 4040 |
| card | wait | 31 | 0 | 14 |
| card | display | 31 | 4 | 10 |
| order | receive | 75 | 38 | 4012 |
| order | wait | 75 | 12 | 20 |
| order | display | 75 | 3 | 16 |
| price_proposal | receive | 8 | 43 | 53 |
| price_proposal | display | 8 | 4 | 11 |
| message_in | display | 4 | 1 | 2 |

Labels are gold (infer 0) and speech uses the rule reader, so infer times here are not model times.
product_seen infer comes from the stored vision rows.

### Situations

| # | run | triggered | passed | event id | what |
|---|---|---|---|---|---|
| 1 | FAIL | 10 | 9 | – | items with a card per loop out of 10 |
| 2 | FAIL | 8 | 5 | card_a54b9408528442388df4 | proposals on a card that already had a price |
| 3 | FAIL | 28 | 12 | c5-dry-1028-024114.c5-1-016 | reference accuracy 0.43 on n 28 |
| 4 | FAIL | 4 | 1 | c5-dry-1028-024114.c5-1-010 | ask_variant within 5 s; stock question answered 0 / 13 |
| 5 | FAIL | 2 | 1 | c5-dry-1028-024114.c5-1-035 | late comments tied to the card shown when written |
| 6 | PASS | 1 | 1 | c5-dry-1028-024114.u1-44 | a price wait happened |
| 7 | PASS | 1 | 1 | c5-dry-1028-024114.c5-1-063 | a "do not cancel" comment leaves the order open |
| 8 | PASS | 1 | 1 | mi.m-c5-dry-1028-024114-c5-1-m04.o-c5-1-060.reply | CANCEL quick reply, then CANCELLED |
| 9 | PASS | 4 | 4 | c5-dry-1028-024114.c5-1-002 | spelled-out phone hidden within 5 s |
| 10 | PASS | 1 | 1 | mi.m-c5-dry-1028-024114-c5-1-m01.o-c5-1-006.reply.o-c5-1-006.approve | ok after the card changed, NEEDS_SELLER, confirm again |
| 11 | TEST_ONLY | 0 | 0 | – | schema and 429: tests only |
| 12 | PASS | 2 | 2 | mi.m-c5-dry-1028-024114-c5-1-m02.o-c5-1-016.reply | CONFIRMED with the receipt at the confirmed version |

The second column (code) reads NOT_RUN in the scenarios file: the scorer lists each situation's test files but
does not run them. Those files are part of `make check` at this commit (401 passed).

Definition question for review: situation 2 counts a replaced proposal as a fail (`measure/c5_report.py`); 3 of
the 8 are replaced. The definition is unchanged here.

### With and without Keviq (ước tính theo giả định)

| what | without | with |
|---|---|---|
| shop actions | 43 | 28 |
| shop minutes | 34.3 | 1.9 |
| orders not taken up in 60 s | không ước tính | 0 |

Assumptions: 60 s per order and 20 s per question by hand; a tap takes 4.04 s (the measured median handling time).

## The 30 gold order comments

The same 30 gold order comment ids are reconciled in every run; none is dropped or changed.

| gold id | gold item | gold kind | C dry outcome | C dry card item | in n | C dry right | why wrong (C dry) | replay A2 right |
|---|---|---|---|---|---|---|---|---|
| 001 | – | unresolved | unresolved | – | no | – | – | – |
| 006 | SP01 | current_card | order | SP01 | yes | yes | – | yes |
| 010 | SP01 | current_card | order | SP01 | yes | yes | – | yes |
| 016 | SP02 | current_card | order | SP01 | yes | no | item_card_live_after_comment | no |
| 018 | SP01 | previous_card | order | SP01 | yes | yes | – | yes |
| 021 | SP02 | current_card | order | SP02 | yes | yes | – | yes |
| 024 | SP02 | current_card | order | SP02 | yes | yes | – | yes |
| 029 | SP03 | current_card | order | SP02 | yes | no | no_card_for_item | no |
| 031 | SP02 | previous_card | order | SP01 | yes | no | item_card_live_before_comment | no |
| 032 | SP03 | current_card | ask_variant | SP02 | yes | no | no_card_for_item | no |
| 035 | SP03 | current_card | order | SP02 | yes | no | no_card_for_item | no |
| 036 | – | unresolved | unresolved | – | no | – | – | – |
| 041 | SP04 | current_card | order | SP02 | yes | no | item_card_live_after_comment | no |
| 043 | SP04 | current_card | order | SP02 | yes | no | item_card_live_after_comment | yes |
| 045 | SP03 | previous_card | order | SP02 | yes | no | no_card_for_item | no |
| 047 | SP04 | current_card | order | SP04 | yes | yes | – | yes |
| 048 | SP04 | current_card | order | SP04 | yes | yes | – | yes |
| 054 | SP05 | current_card | order | SP04 | yes | no | item_card_live_after_comment | yes |
| 055 | SP04 | previous_card | order | SP04 | yes | yes | – | yes |
| 056 | SP05 | current_card | order | SP05 | yes | yes | – | yes |
| 060 | SP06 | current_card | order | SP05 | yes | no | item_card_live_after_comment | no |
| 062 | SP06 | current_card | order | SP06 | yes | yes | – | yes |
| 071 | SP07 | current_card | ask_variant | SP06 | yes | no | item_card_live_after_comment | yes |
| 074 | SP07 | current_card | order | SP07 | yes | yes | – | yes |
| 078 | SP08 | current_card | order | SP07 | yes | no | item_card_live_after_comment | yes |
| 082 | SP09 | current_card | order | SP08 | yes | no | item_card_live_after_comment | yes |
| 085 | SP09 | current_card | order | SP09 | yes | yes | – | yes |
| 091 | SP10 | current_card | order | SP09 | yes | no | item_card_live_after_comment | yes |
| 095 | SP01 | current_card | order | SP10 | yes | no | item_card_live_before_comment | no |
| 098 | SP01 | current_card | order | SP10 | yes | no | item_card_live_before_comment | no |

Ids are short for `c5-1-<id>`. "why wrong" values: no_card_for_item, the item had no card in the loop;
item_card_live_after_comment, the item's card went live after the comment arrived; item_card_live_before_comment,
the item's card had been live earlier but another card was live at the comment.

Denominators, by id:

- Replay A2 (session `replay-c5-test1-1028-020339`, labels injected): unresolved 2 (001, 036), orders 27,
  ask_variant 1 (032). n 28 under core.c3, right 18.
- C dry (this run, the real Labeler path with gold labels): unresolved 2 (001, 036), orders 26, ask_variant 2
  (032, 071). n 28, right 12. 071 is an order in A2 and an ask_variant here: at the comment the SP06 card was live,
  and the SP07 card went live after it.
- C live: not run (no paid calls tonight).

So 27 is A2's order count and 28 is the shared n (orders plus ask_variant, core.c3's definition). The gate
denominator is the existing definition, with no case removed.

C dry has 6 more wrong than A2: 043, 054, 071, 078, 082, 091, all item_card_live_after_comment. That is consistent
with the documented card hold (docs/DECISIONS.md: HOLD_MS = D + H = 4168 ms, a known limit). On the real clock a
card goes live up to about 4.2 s after the line that opens it, while the A2 replay put it live 5 ms after the
speech end. This explains the gap. It does not show an order-flow regression, and it does not replace the A2
replay.

## Scorer fixes in this PR

Both fixes reuse an existing definition, have a test that is red on the old code and green after, and use the
same manifest and gold. Command: `MODEL_API_KEY= uv run pytest -q tests/test_c5_score.py`.

1. Card to item, references and order ids. A card stands for the gold item of the frame (product_seen) that opened
   it, not the item it overlapped longest over its life. References use core.c3's field (the card the label
   drafted) and count a comment with no card as wrong. The order ids cover all 30 gold ids and share one n with the
   references.
   - Old scorer sha256 `c06a7d82a1d6a809d4e73f563d265a8dd9d5562b08b0bce1d85cbb4b68ed5ab9`: `6 failed, 5 passed in 0.07s`.
   - New `517e6d725cfa696f880c22b275bfc1ffa91520c30b9aa07d85474c513f69e025`: `11 passed in 0.03s`.
2. Proposal outcome (D-024). A proposal closes at the first version newer than its card_version that is retired
   or has a price other than its current_price. Before, the scorer ordered versions by the proposal's ts_ms, which
   is its speech time, and it trusted `edited_by`, which worker versions copy. That gave negative handling times
   (−52 ms, −16026 ms).
   - Old `517e6d72…`: `2 failed, 11 passed in 0.06s`.
   - New `e4ac62f5…`: `13 passed in 0.03s`.

The first-run files (scored before these fixes) are kept unchanged and local, not committed.

| file | sha256 |
|---|---|
| c5-dry-1028-023003.json | `3388d9032432c8ae90ce34b0e3c9adde75e94f5de845718427f073b539a1010e` |
| c5-dry-1028-023003-scenarios.json | `a28e401884d438b65ee4fed9e2088f1628350efffba53d632e244ead176d3c7e` |
| c5-dry-1028-023003-comments.jsonl | `03b427e1b3d9c4d636017bd6ac98b547a997e2f3ef8d6af8a204b8e59882efc0` |
| c5-dry-1028-024114.json | `bd3f4a25736bc0181b0bdbfc2f224d4dc8eaf362430e95415b402e566fcb6af9` |
| c5-dry-1028-024114-scenarios.json | `b74f77ab31dba28af29bdf3a1d8a72aa634d57b6790e82e4d53acdd7239b486d` |
| c5-dry-1028-024114-comments.jsonl | `b7234ccb890b1427f7ee9ca066da0f07f434109a5427b744c36a65215edf59fa` |

The A2 replay's public note is referenced by hash only:
`b7a2ae4b8528ef7825c3ba6a929a1ca3e1c04a3f09986f1b7107db3b3b404f74`.

## Caveats

- One dry loop, not five. Gold labels, stored vision rows and the rule price reader stand in for the models.
- The loop restart drops worker state inside one process. A continuous 30-minute worker run is not proven; the
  demo is one pass without a restart.
- `receipts_ok` needs an image; a dry run sends none.
- 4 of 9 cards have no SKU under D-026's one-match rule (colour outside the vision vocabulary, or two items with
  the same category and colour; see D-026).
- The shop and the customers are scripted (4.0 s per shop action), so the intervention count and the handling
  times follow the script.
- Reference n is 28 per loop; the target needs at least 50, so one loop cannot meet it.
