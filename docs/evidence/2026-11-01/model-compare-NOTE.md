# Product identification: Spark minimal, Spark high and Wonder on one frozen frame set (2026-11-01)

This is a measurement. No production code, prompt, schema or price changed on this branch; `git diff origin/main --stat -- worker app meta orders contracts intent.py` prints nothing.
The comparison code is `measure/model_compare.py` (runner, cap, ledger sink) and `measure/model_compare_score.py` (scoring), tested by `tests/test_model_compare.py` with no network calls.

Founder's request (2026-11-01), verbatim: "lập phép so sánh Spark minimal → Spark high → Wonder, trên cùng đầu vào và schema đã phân biệt được sản phẩm, với trần thử nghiệm nhỏ, chẳng hạn 5 USD. Đo đúng món, từ chối đoán khi mơ hồ, độ trễ và chi phí."

## Result in short

- **Schema moved the result. The model choice barely moved it.** Of 50 clear frames, the production schema A gets 22 correct with both Spark efforts. B0 (same information, new schema) gets 31-34 and B (B0 plus the item list) gets 34-35, in all three configurations. With the schema fixed, two configurations disagree on 3 or 4 of the 70 frames.
- **Almost every wrong pick is one item.** Each arm makes 6 or 7 wrong picks of SP03 read as SP02; spark-minimal/B0 makes one more (SP04 read as SP01). The catalog calls SP03 "giày xám". On frame t=97, viewed by eye, the shoes look cream or off-white, next to SP02 "giày trắng". SP09 ("bàn phím đen") has green or teal keycaps on t=277, and the B arms answer `unsure` there. Both are questions about the catalog data, not about model reading (see "Catalog against camera").
- **Abstention works where nothing is for sale.** No arm makes a wrong pick on the 6 no-product frames or on the 14 boundary frames.
- **The 0.5 confidence gate changes no verdict in any arm.** The lowest confidence on a pick is 0.6 to 0.9, depending on the arm.
- **Cost and latency.** Per call, spark-minimal costs about 0.0013 USD, spark-high 0.0043-0.0053 and wonder-high 0.016-0.019. Median latency is 1.6-1.8 s, 4.0-4.1 s and 3.9-4.2 s.
- **The pre-registered selection rule picks wonder-high/B.** It ties spark-high/B0 on wrong picks (6 each) and wins on correct clear frames (34 against 31). Its lead over spark-minimal/B is one frame: on t=103 (SP03) wonder-high/B abstains and spark-minimal/B picks SP02. That lead costs about 15 times more per call. One video cannot separate the two.
- **For the 13 order comments that got no card in run 1615**, the B and B0 schemas name the right item code on the comment's frame for 5-6 of them (A: 3). Three or four would name a wrong code (SP03 read as SP02). Naming the item is not an order: see the per-comment table.
- **Spend, from the model_call ledger:** 3.7242853 USD over 560 calls: spark-minimal 0.27568665, spark-high 0.98927865, wonder-high 2.45932. This has not been reconciled with Billing. The 5.00 USD figure is the cap for all three configurations together, not an amount spent.

The Founder asked for two decisions to stay separate. The data for each:

- **Running cost (operations):** spark-minimal/B reads 35 of 50 clear frames correctly with 7 wrong picks (all SP03), at 0.00131 USD per call and a 1.7 s median.
- **Quality search:** on this video Wonder read no more clear frames correctly than Spark under B. The errors left over trace back to catalog descriptions.

Neither decision is taken here. Nothing on this branch changes production.

## Wonder exception

Rule 5 of `AGENTS.md` bars Muse Wonder this week, and `meta/pricing.py` lists it in `BLOCKED`. The Founder's authorization, verbatim:

- Question: "Luật 5 cấm dùng Muse Wonder tuần này. Anh có cho phép ngoại lệ dùng Wonder chỉ trong thử nghiệm so sánh model này, trần tổng 5 USD, không đưa vào sản phẩm không? Câu trả lời của anh sẽ được trích nguyên văn vào hồ sơ."
- Founder: "Cho phép, trần 5 USD"

Scope: this experiment only. The 5.00 USD cap covers all three configurations together.

How the exception works:

- `meta/pricing.py` is unchanged and still blocks Wonder.
- `measure/model_compare.py` (`wonder_allowed`, lines 148-155) rebinds two names only while the Wonder phase runs: `meta.client.BLOCKED` (a copy without Wonder) and `meta.client.cost_usd` (the experiment price). It restores both afterwards. The test is `test_wonder_is_allowed_and_priced_only_inside_the_experiment`.
- The Wonder price is used only in that module: input 4.00 USD and output 20.00 USD per million tokens.
- The cached input price is not published, so cached input is charged as full input. Wonder had 31,280 cached input tokens. If the real cached price is lower, the Wonder figure above is high by up to 0.12512 USD.
- Wonder accepted effort `high`, so the fallback planned in the manifest (the model's default effort) was not needed.

## Setup

| configuration | model | effort |
|---|---|---|
| spark-minimal | muse-spark-1.3 | minimal |
| spark-high | muse-spark-1.3 | high |
| wonder-high | muse-wonder-1.0 | high |

| schema | what the model returns | how it is scored |
|---|---|---|
| A | production `worker/vision/spark.py` SCHEMA and prompt: category (`other` for the controllers and keyboards), colour, confidence | production `match(category, colour)` on the catalog: one item gives a pick; several give `cannot name (collision)`; none gives `cannot name (no_match)` |
| B0 | state (`one_product` / `no_product` / `unclear`), kind (giày, áo, tay cầm, bàn phím, khác, none), colour, confidence; no item list | the one catalog item of that kind and colour |
| B | B0 plus `sku` (SP01..SP10, `unsure`, `none`), with the catalog item list in the prompt | an SP code is a pick; `unsure` or `none` is an abstention |

- Wonder ran B and B0 only. A cannot tell SP07 from SP09, or SP08 from SP10, whatever the model sees: all four are `other` plus black or white.
- **Same information (B0 against A):** neither prompt holds the item list. B0 only replaces the shape of the answer. B adds the item list. So "A to B0" is the schema's share and "B0 to B" is the information's share.
- **No gold in any prompt.** Every call sends `last=None` (no previous card). `test_prompts_carry_no_gold_and_only_b_lists_the_items` checks both.
- **Logging.** Every call goes through `MetaClient.responses`, whose `_log` emits a `model_call` event (session `model-compare-1101`, purpose `model_compare`). The sink is a JSONL file that captures the exact event payload. No app and no database were used, so ports 8000/8001, the `keviq` database, the `keviq-live` compose project and `keviq_paid_test` were not touched.
- **The project's daily meter did not see these events.** Add 3.7242853 USD to today's model spend by hand.
- **Input.** 360x640 JPEG frames of test1 from local gold data (not in git, never copied into the repo). The manifest stores each frame's sha256.
- **Concurrency.** Spark ran 3 calls at a time. Wonder ran 1 at a time, to keep the cap's in-flight holds small. Latency figures therefore come from different load conditions.
- **Retries.** No schema retry happened: 560 calls made 560 POSTs.

## Frozen protocol

- The frame manifest `model-compare-manifest.json` was committed alone in `e061ad1` at 18:38:01 +07:00. The first paid call is logged at 18:38:08 (ledger `ts_ms`). The manifest records `code_commit` 3aeb64c and the prompt hashes A `6f00b39abb819af6`, B0 `c5d058e5a78e490b`, B `411636d3b48de893`.
- At the start of each run, `check_manifest` selects the frames again from the labels and checks every frame's sha256 against the manifest. It stops on any difference. Both runs passed this check, and no frame was reselected.
- **Selection** (from the manifest):
  - Clear: 4 non-boundary seconds per item, evenly spaced.
  - Boundary: the second nearest each product change's cue.
  - No product: 6 evenly spaced empty seconds.
  - Order comments: each comment's own second (current card), or the last clear second of its item before the comment (previous card).
- **Strata:**
  - `single_unique_colour` (SP03, SP06): 11 frames;
  - `same_colour_fashion` (SP01, SP02, SP04, SP05): 19;
  - `same_colour_other` (SP07-SP10): 20;
  - `boundary`: 14;
  - `no_product`: 6.

  Total: 70 frames, of which 50 are clear.
- **Code after the freeze.** `19c3acc` and `472f30a` change only scoring and reporting: per-configuration spend from the ledger, gated verdicts in the comment links, and checking stock per variant. The call path and the prompts are those of the frozen commit.

## Cap accounting

Rule (from the manifest):

- **Before each call:** spent + unknown + held + 2 x the worst-case POST must not exceed the cap. Otherwise the run stops cleanly.
- **Before each POST:** the meter holds that POST's worst case.
- **After a POST:** a logged POST releases its hold. A POST with no reply keeps its worst case as unknown spend.
- **Worst-case POST:** max(1500, 1.2 x the largest tokens_in seen) x the input price, plus max(floor, 2 x the largest tokens_out seen) x the output price. Floors: spark-minimal 1000, spark-high and Wonder 8000.

The client sends no output-token limit, so this worst case is an estimate, not a bound. The Spark phase had its own cap of 2.50 USD, to leave room for Wonder. The total cap was 5.00 USD.

Outcome: no call was refused and no POST went without a reply (`unknown_reserve` 0).

## Commands and output

Run from the worktree root. `.env` is a gitignored symlink to the main checkout's file (`git check-ignore -v .env` prints `.gitignore:2:.env	.env`); its contents were not read. `<gold>` is `data/local/test1` in the main checkout, and the absolute path was used. `<worktree>` replaces the worktree's absolute path in the output.

```
$ uv run python -m measure.model_compare manifest --gold <gold>
70 frames {'boundary': 14, 'single_unique_colour': 11, 'same_colour_fashion': 19, 'same_colour_other': 20, 'no_product': 6} -> <worktree>/docs/evidence/2026-11-01/model-compare-manifest.json
```

The manifest was committed (`e061ad1`) before the next command.

```
$ uv run python -m measure.model_compare run --phase spark --gold <gold> --concurrency 3
phase spark: cap 2.5 USD, spent before 0.000000 USD
1/420 t=13 spark-minimal/B0 ok 0.00123 USD 1810 ms spent=0.0012
2/420 t=13 spark-minimal/A ok 0.00126 USD 2543 ms spent=0.0025
3/420 t=13 spark-minimal/B ok 0.00125 USD 2571 ms spent=0.0037
4/420 t=13 spark-high/B0 ok 0.00248 USD 3726 ms spent=0.0062
...
419/420 t=349 spark-high/B ok 0.00368 USD 3207 ms spent=1.2603
420/420 t=349 spark-high/A ok 0.00471 USD 5057 ms spent=1.2650
{"planned": 420, "already_done": 0, "called": 420, "stopped": null, "spent_before": 0, "spent_after": 1.264965, "unknown_reserve": 0}

$ uv run python -m measure.model_compare run --phase wonder --gold <gold> --concurrency 1
phase wonder: cap 5.0 USD, spent before 1.264965 USD
1/140 t=13 wonder-high/B ok 0.01699 USD 4672 ms spent=1.2820
2/140 t=97 wonder-high/B ok 0.02309 USD 7363 ms spent=1.3050
3/140 t=21 wonder-high/B ok 0.01485 USD 4102 ms spent=1.3199
4/140 t=270 wonder-high/B ok 0.02835 USD 6303 ms spent=1.3483
...
138/140 t=9 wonder-high/B0 ok 0.01637 USD 4262 ms spent=3.6916
139/140 t=114 wonder-high/B0 ok 0.01717 USD 7069 ms spent=3.7088
140/140 t=349 wonder-high/B0 ok 0.01549 USD 4819 ms spent=3.7243
{"planned": 140, "already_done": 0, "called": 140, "stopped": null, "spent_before": 1.264965, "spent_after": 3.724285, "unknown_reserve": 0}
```

Each progress line matches one row of `model-compare-calls.jsonl` (cost, latency, event ids). The full console logs are not kept.

```
$ uv run python -m measure.model_compare score
arm | n | clear ok/wrong/abst/cant/err | boundary ok/wrong/no-pick | no_product wrong/abst | wrong all | gated wrong | p50 ms | p95 ms | USD | USD/350
spark-high/A | 70 | 22/7/2/19/0 | 4/0/10 | 0/6 | 7 | 7 | 4064 | 7294 | 0.3039 | 1.519688
spark-high/B | 70 | 35/7/8/0/0 | 8/0/6 | 0/6 | 7 | 7 | 4107 | 14357 | 0.3674 | 1.83676
spark-high/B0 | 70 | 31/6/6/7/0 | 6/0/8 | 0/6 | 6 | 6 | 3994 | 11939 | 0.3180 | 1.589946
spark-minimal/A | 70 | 22/7/2/19/0 | 4/0/10 | 0/6 | 7 | 7 | 1787 | 5600 | 0.0977 | 0.488659
spark-minimal/B | 70 | 35/7/8/0/0 | 7/0/7 | 0/6 | 7 | 7 | 1688 | 5142 | 0.0915 | 0.457401
spark-minimal/B0 | 70 | 33/8/4/5/0 | 8/0/6 | 0/6 | 8 | 8 | 1646 | 3465 | 0.0865 | 0.432374
wonder-high/B | 70 | 34/6/10/0/0 | 8/0/6 | 0/6 | 6 | 6 | 4159 | 14488 | 1.3561 | 6.7803
wonder-high/B0 | 70 | 34/7/5/4/0 | 7/0/7 | 0/6 | 7 | 7 | 3914 | 12058 | 1.1033 | 5.5163
{"best": "wonder-high/B", "ledger": {"model_calls": 560, "ok": 560, "usd": 3.7242853, "by_model": {"muse-spark-1.3": {"calls": 420, "usd": 1.2649653}, "muse-wonder-1.0": {"calls": 140, "usd": 2.45932}}, "no_reply_posts": 0, "by_config": {"spark-high": {"model_calls": 210, "usd": 0.98927865}, "spark-minimal": {"model_calls": 210, "usd": 0.27568665}, "wonder-high": {"model_calls": 140, "usd": 2.45932}}, "billing": "not reconciled with Billing"}}
```

`score` writes `model-compare-score.json`. The tables below are taken from that file.

## Spend (model_call ledger)

| configuration | model_call events | USD |
|---|---|---|
| spark-minimal | 210 | 0.27568665 |
| spark-high | 210 | 0.98927865 |
| wonder-high | 140 | 2.45932 |
| total | 560 | 3.7242853 |

- Not reconciled with Billing.
- Before the experiment: 0. After the Spark phase: 1.264965. After the Wonder phase: 3.724285.
- Cap (a limit, not spend): 5.00 USD for all three configurations together. 1.2757147 USD of it was not used.
- Each row in `model-compare-calls.jsonl` lists its `event_ids`. All 560 events map to one row each; none is unattributed.

## Behaviour per stratum

Each cell is correct / wrong / no pick, where no pick = abstain + cannot name. Gold per frame comes from the frozen manifest.

| arm | single_unique_colour (11) | same_colour_fashion (19) | same_colour_other (20) | boundary (14) | no_product (6) | clear (50) | all (70) |
|---|---|---|---|---|---|---|---|
| spark-minimal/A | 4/7/0 | 18/0/1 | 0/0/20 | 4/0/10 | 0/0/6 | 22/7/21 | 26/7/37 |
| spark-high/A | 4/7/0 | 18/0/1 | 0/0/20 | 4/0/10 | 0/0/6 | 22/7/21 | 26/7/37 |
| spark-minimal/B0 | 3/7/1 | 15/1/3 | 15/0/5 | 8/0/6 | 0/0/6 | 33/8/9 | 41/8/21 |
| spark-high/B0 | 3/6/2 | 15/0/4 | 13/0/7 | 6/0/8 | 0/0/6 | 31/6/13 | 37/6/27 |
| wonder-high/B0 | 4/7/0 | 15/0/4 | 15/0/5 | 7/0/7 | 0/0/6 | 34/7/9 | 41/7/22 |
| spark-minimal/B | 4/7/0 | 16/0/3 | 15/0/5 | 7/0/7 | 0/0/6 | 35/7/8 | 42/7/21 |
| spark-high/B | 4/7/0 | 16/0/3 | 15/0/5 | 8/0/6 | 0/0/6 | 35/7/8 | 43/7/20 |
| wonder-high/B | 4/6/1 | 15/0/4 | 15/0/5 | 8/0/6 | 0/0/6 | 34/6/10 | 42/6/22 |

**Gated view (production gate: a pick below confidence 0.5 becomes an abstention).** It matches the table above cell for cell in every arm. Lowest confidence on a pick, per arm:

- spark-minimal/A 0.7, spark-high/A 0.6;
- spark-minimal/B0 0.75, spark-high/B0 0.9, wonder-high/B0 0.75;
- spark-minimal/B 0.75, spark-high/B 0.75, wonder-high/B 0.75.

**Why no pick was made:**

- A: `none` 14, `collision` 19, `no_match` 4 (same for both efforts).
- B: `none` 13-14, `unsure` 7-8.
- B0:
  - spark-minimal: `no_product` 13, `no_match` 5, `unclear` 3;
  - spark-high: `no_product` 13, `no_match` 6, `unclear` 5, `collision` 1, kind `khác` 2;
  - wonder-high: `no_product` 14, `no_match` 4, `unclear` 4.

Scoring rules:

- **Boundary frames:** a pick of either side of the change counts as correct. This is lenient, because the boundary gold is uncertain.
- **No-product frames:** any pick is wrong.

## Gain: schema against information

Correct clear frames out of 50:

| configuration | A | B0 (schema only) | B (schema + item list) | share of the gain from the schema | share from the item list |
|---|---|---|---|---|---|
| spark-minimal | 22 | 33 | 35 | +11 of +13 (85%) | +2 (15%) |
| spark-high | 22 | 31 | 35 | +9 of +13 (69%) | +4 (31%) |
| wonder-high | not run | 34 | 34 | n/a | 0 |

- **Where the gain comes from.** Neither change reduces wrong picks: the SP03 errors stay. The whole gain is frames where A could not name an item. On `same_colour_other`, A gets 0 of 20 (19 collisions, 1 `none`) and B0/B get 13-15.
- **Over all 70 frames:** spark-minimal goes 26 → 41 → 42 and spark-high 26 → 37 → 43.
- **B0 to B changes the form of the non-answers.** On SP09, B0 says `no_match` (the black keyboard is seen as green or teal) and B says `unsure`. Both leave no card.

## Model at a fixed schema (same 70 frames)

| pair | frames where the verdicts differ |
|---|---|
| wonder-high/B vs spark-minimal/B | t=103 abstain vs wrong; t=169 correct vs abstain; t=339 abstain vs correct |
| wonder-high/B vs spark-high/B | t=103 abstain vs wrong; t=256 correct vs abstain; t=270 abstain vs correct; t=339 abstain vs correct |
| spark-minimal/B vs spark-high/B | t=169 abstain vs correct; t=256 correct vs abstain; t=270 abstain vs correct |
| wonder-high/B0 vs spark-minimal/B0 | t=93 abstain vs correct; t=139 correct vs wrong; t=205 correct vs cannot name; t=339 abstain vs correct |

## Selection rule (fixed before the run)

The rule:

1. Keep the arms that read every manifest frame, with at most 5% error rows, and with no pick on at most half of the clear frames.
2. Among those, take the fewest wrong picks over all frames (ungated).
3. On a tie, take more correct clear frames.
4. If still tied, take the lower USD.

Result: wonder-high/B. It has 6 wrong picks, tied with spark-high/B0; it then wins on correct clear frames, 34 against 31. Every other arm has 7 or 8 wrong picks. All of these margins are one frame on one video.

**Post-hoc view (decided after seeing the data, not part of the rule):** with the 7 SP03 frames removed, 7 of the 8 arms make 0 wrong picks. spark-minimal/B0 makes 1 (t=139, SP04 read as SP01).

## Catalog against camera

| frame | gold | catalog text | appearance (checked by eye) | what the arms do |
|---|---|---|---|---|
| t=97 | SP03 | "giày xám" | cream or off-white shoes | all 8 arms pick SP02 ("giày trắng") |
| t=277 | SP09 | "bàn phím đen" | green or teal keycaps | B arms `unsure`, B0 arms `no_match` |

- **Who checked:** these two frames were viewed by eye on this branch. No one else has confirmed them.
- **SP03:** 0 of 7 clear frames correct in every arm, and every wrong pick in 7 of the 8 arms.
- **SP09:** 0 to 3 of 6 correct.
- **What needs deciding:** whether the catalog text or the gold should change. That is a data decision for the Founder. It is not a model setting.

## Cost and latency

| arm | latency p50 / p95 / max ms | tokens in mean | tokens out mean / max | USD | USD per call | USD per 350 frames (projection) |
|---|---|---|---|---|---|---|
| spark-minimal/A | 1787 / 5600 / 24099 | 500 | 203.8 / 369 | 0.0977 | 0.00140 | 0.489 |
| spark-minimal/B0 | 1646 / 3465 / 7075 | 528 | 168.3 / 481 | 0.0865 | 0.00124 | 0.432 |
| spark-minimal/B | 1688 / 5142 / 5923 | 668 | 197.0 / 716 | 0.0915 | 0.00131 | 0.457 |
| spark-high/A | 4064 / 7294 / 8593 | 500 | 897.0 / 1671 | 0.3039 | 0.00434 | 1.520 |
| spark-high/B0 | 3994 / 11939 / 44266 | 528 | 946.5 / 4240 | 0.3180 | 0.00454 | 1.590 |
| spark-high/B | 4107 / 14357 / 55343 | 668 | 1124.3 / 4388 | 0.3674 | 0.00525 | 1.837 |
| wonder-high/B0 | 3914 / 12058 / 21780 | 528 | 682.4 / 2143 | 1.1033 | 0.01576 | 5.516 |
| wonder-high/B | 4159 / 14488 / 21619 | 668 | 835.0 / 3026 | 1.3561 | 0.01937 | 6.780 |

- **What latency means here:** wall time per call on the client. Spark ran at concurrency 3 and Wonder at 1.
- **What "USD per 350 frames" means:** USD per call x 350. It is a projection for one pass over the 350-second video at one frame per second, not a measurement.
- **What the USD figures are:** ledger prices, not Billing.

## The 13 order comments with no card in run 1615

Comments and gold orders come from `data/comments/c5/stream-1.jsonl`; run 1615 is `docs/evidence/2026-10-31/rescore-paid-1031-1615.json`. "Read" is one model call on the comment's frame:

- **Current card:** the frame at the comment's second.
- **Previous card:** the last clear second of the gold item before the comment.

A "valid-code card" means that read names the gold SP code. The production card path (frame cadence, stability, timing against the comment) is not replayed. Arm A's reads name the right code for 031, 095 and 098, yet run 1615 showed no card for them. So treat the column as an upper bound on what the card path would show.

"Still blocks" lists what stands between the comment and an order once the item is known. It is computed from the comment's gold order fields and the catalog, with no model reading of the comment text. A spoken price change is assumed to hold from its start until the end of the session.

| comment | gold order | read: spark-minimal/A | read: spark-minimal/B | read: wonder-high/B (selected) | valid-code card (wonder-high/B, gated) | still blocks once the item is known | order? |
|---|---|---|---|---|---|---|---|
| c5-1-029 | SP03 size 41 x1, current card | SP02 (wrong) | SP02 (wrong) | `unsure` | no | nothing else | no card. With a wrong card (A, spark-minimal/B), a draft would name the wrong item |
| c5-1-031 | SP02 size 40 x1, previous card | SP02 | SP02 | SP02 | yes | the comment names an earlier item ("vừa nãy"), not the one on screen | knowing SP02 is not an order: the earlier item must be matched to the comment first |
| c5-1-032 | SP03, no size, x1, current card | SP02 (wrong) | SP02 (wrong) | SP02 (wrong) | no | size missing (41 or 42): ask the customer | no order: wrong item, and the size is still missing |
| c5-1-035 | SP03 size 42 x1, current card | SP02 (wrong) | SP02 (wrong) | SP02 (wrong) | no | nothing else | no order: wrong item |
| c5-1-045 | SP03 size 41 x1, previous card | SP02 (wrong) | SP02 (wrong) | SP02 (wrong) | no | earlier item ("vừa nãy") | no order: wrong item and an earlier-item reference |
| c5-1-071 | SP07 x1, current card | collision | SP07 | SP07 | yes | nothing else | the card names SP07. An order still needs the comment's intent and the customer's confirmation (`orders/rules.py`) |
| c5-1-074 | SP07 x1, current card | collision | SP07 | SP07 | yes | nothing else | as 071: item known, order not created |
| c5-1-078 | SP08 x1, current card | collision | SP08 | SP08 | yes | nothing else | as 071: item known, order not created |
| c5-1-082 | SP09 x1, current card | no_match | `unsure` | `unsure` | no | nothing else | no card (SP09 colour in the catalog against the camera) |
| c5-1-085 | SP09 x1, current card | no_match | `unsure` | `unsure` | no | spoken price 600000 vs catalog 650000 from 286.1 s: needs the seller's approval | no card. Even with one, the price change needs the seller |
| c5-1-091 | SP10 x1, current card | collision | SP10 | SP10 | yes | nothing else | as 071: item known, order not created |
| c5-1-095 | SP01 size 40 x1, current card | SP01 | `unsure` | `unsure` | no | spoken price 220000 vs catalog 250000 from 37.5 s: needs the seller's approval | no card under B. Even with one, the price change needs the seller |
| c5-1-098 | SP01 size 40 x3, current card | SP01 | SP01 | `unsure` | no | quantity 3 > stock 2 (the seller decides); spoken price 220000 vs 250000: needs the seller's approval | no card under wonder-high/B; spark-minimal/B names SP01, but stock and price still need the seller |

Valid-code cards out of 13:

- 3 for the A arms;
- 6 for every Spark B and B0 arm;
- 5 for the Wonder arms.

Wrong-code cards (SP03 read as SP02):

- 4 for every A arm, spark-minimal/B0, spark-minimal/B, spark-high/B and wonder-high/B0;
- 3 for spark-high/B0 and wonder-high/B.

In every case, recognising the item is not creating an order. The comment's intent, the reference and the variant must still be resolved, and `orders/rules.py` still requires the customer's confirmation and the seller's approval for price and stock.

## Biases and limits

- **Tuning.** The production prompt (A) was tuned on this same video, test1, so A is favoured here. The B0 and B prompts were written on this branch before any paid call and frozen by hash. Their author knew the catalog and the run-1615 failure, so they are not blind to test1 either.
- **Sample size.**
  - One video: 70 frames, 4-7 clear frames per item. A one-frame difference is within noise.
  - The comment frames add frames to the items customers ordered: SP03 has 7 clear frames and SP09 and SP01 have 6. So the SP03 catalog issue weighs 7 of the 50 clear frames.
- **Context.** `last=None` on every call, so this is a per-frame read with no previous-card context, unlike the production card path.
- **Input quality.** Frames are 360x640, and the boundary gold is uncertain (hence the lenient boundary rule).
- **Cap estimate.** No output-token limit is sent, so the worst case per POST is an estimate. No call's output reached the floor of its configuration's worst case (largest tokens_out: spark-minimal 716 against 1000, spark-high 4388 against 8000, Wonder 3026 against 8000).
- **Spend tracking.** The project's daily and total meters were not consulted. These events went to a JSONL sink, not to the database.
- **Comments.** No text-only (comment) model call was made. The comment section uses gold order fields.

## Not verified

- The spend has not been reconciled with Billing.
- The catalog against camera finding rests on two frames viewed by eye. The Founder has not confirmed it.
- Whether the production card path would show a card for the comments marked "valid-code card". It was not replayed.
- Any other video, lighting or catalog.
- Whether the latency differences hold at equal concurrency.

## Files

- `model-compare-manifest.json`: frozen frames, strata, gold, prompts, schemas, rules (commit `e061ad1`).
- `model-compare-calls.jsonl`: one row per call (560): the model's JSON, tokens, cost, latency, event ids. No images, no secrets.
- `model-compare-model-calls.jsonl`: the 560 `model_call` events exactly as `_log` emitted them.
- `model-compare-score.json`: per-arm and per-stratum counts, paired comparisons, scored rows, ledger totals, comment links.
