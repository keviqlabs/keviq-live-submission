# Rescore baseline — rescore-base-1029-194618

Unpaid replay of the stored observation set of `diag-test1x1-1028-1531` against the app, worker and order
code of main `a767f02`. Purpose: a 0 USD baseline for the same 1,096 events before the media-time attachment
change (PR #61) lands, so that its rescore can be compared number for number.

| Item | Value |
| --- | --- |
| Model calls | 0 — `MODEL_API_KEY` blank, `model_key_set: false` in the run file, `model_call` rows for the session: 0 (SQL `(0 rows)`, printed by the runner) |
| Run id | `rescore-base-1029-194618` |
| Revision | `b5438ed5752138358eb463fd6109b2631db5f0bb` = `a767f02` + measure-only replay support (below) |
| `make check` on this revision | rc 0 — `All checks passed!`, `1359 passed, 2 xfailed in 71.77s` |
| Observation set | `observations.jsonl` sha256 `f48b69a95b969504dd809a769cd622849921237d17c52a1694bf758f67465f9a`, 1,096 events (11 product_seen, 64 speech, 351 frames), kept outside git |

## What is replayed and what is not

- The diag run's `product_seen` and `speech` events are re-emitted with their original offsets from the diag's
  first frame (`measure/c5_frozen.py`, new on this branch). They carry the diag's labels, so the model-label
  error of 28 October is kept as it was; it is not re-measured here.
- Frames come from the stored test1 frame sequence through the media process, as in every offline run; no
  vision call is made. Comments and the scripted shop are the offline C5 scenario, one loop.
- `git diff --stat a767f02..b5438ed` lists only `measure/c5.py`, `measure/c5_frozen.py`, `measure/c5_live.py`
  and `tests/test_c5_frozen.py`; `app/`, `worker/` and `orders/` are those of `a767f02`.
- The scripted shop of `a767f02` has no item-choice action, so seller SKU choices are 0 by construction.
  The projected intervention rate below adds one choice per card that never received a SKU.

## Execution and isolation

Trial app on `127.0.0.1:8040` (ports 8000, 8001 and 8040 all answered nothing before the start), test
database through the shared lock (`python -m tests.db_lock`), `MODEL_SESSION_CAP_USD=8`,
`MODEL_SESSION_WARN_USD=6`, `CATALOG_PATH=data/comments/c5/catalog.json`.

```
uv run python -m tests.db_lock -- bash rescore-run.sh rescore-base-1029-194618 <observations.jsonl> <out> 8040
DATABASE_URL name: /keviq_test
MODEL_API_KEY set: no
migrations applied: none (up to date)
healthz: {"ok":true,"store":"pg","head_seq":163033}
runner rc=0
```

Runner (`measure.c5 --base-url http://127.0.0.1:8040 --loops 1 --run rescore-base-1029-194618 --observations <observations.jsonl>`):

```
loop 1: t0 1793277982719
loop 1: done, SETTLED/SETTLED behind 0, retired 10, waits {'many': 2}, failures 0
run rescore-base-1029-194618 sha b5438ed5752138358eb463fd6109b2631db5f0bb loops 1
```

Events stored for the session: 802 (frame 351, comment 100, comment_labeled 110, message_out 52, speech 64,
product_seen 11, card 39, order 71, message_in 3, price_proposal 1). The lock was released afterwards.

## Results (one loop, 100 comments, 28 references, 30 order comments)

| Measure | Baseline `a767f02` | Diag 28 Oct (paid, revision 650fe39) |
| --- | --- | --- |
| References right | 8/28 = 0.286 (current_card 6/24, previous_card 2/4) | 7/28 = 0.25 |
| Reference failures | no_card 2; item card live after the comment 11; live before 4; never live 3 | — |
| Receipts, pinned version correct | 2/2 | 2/2 |
| Receipts, right item against gold | 0/2 (o-c5-1-016 card SP01 vs gold SP02; o-c5-1-029 card SP02 vs gold SP03) | 0/2 |
| Order comments by outcome | order 25, unresolved 4, ask_variant 1 | — |
| Abstentions | 4 unresolved drafts → 4 `ask_which_item` messages; 1 `ask_variant` message | — |
| Cards opened | 11 for 10 items; 6 without SKU (SP03, SP07, SP08, SP09, SP10, SP01 return); 0 still draft at loop end | 9; SP07–SP10 without SKU; SP03 reused the SP02 card |
| Duplicate cards per item | SP01: 2 (`card-1-00` at +22 s, `card-1-10` at +333 s, the return) | 0 |
| Seller SKU choices | 0 (no such shop action on this revision) | 0 |
| Interventions, scripted | 19 over 6.38 min = 178.7/hour | 19, 178.62/hour |
| Interventions by cause | variant 8, over_stock 7, price 1, discount 1, card_state 1, other 1 | variant 10, over_stock 7, price 1, discount 1 |
| Projected with one SKU choice per SKU-less card | 25 = 234.96/hour | — |

Needs-seller entries: 18 (variant 8, over_stock 7, discount 1, card_state 1, other 1); 9 of them on a card
without SKU. Reasons as the scorer prints them: `variant size # not on card-#-# v#` 4, `size M` 2, `size L` 1,
`card was draft when the comment was written` 1 (+1 combined with a size), `card was retired when the comment
was written` 1, discount 1, `qty # > stock #` 7.

Reading: the interventions are scripted shop actions over a 6.4 minute loop and are not a measurement of a
human seller. Even without the SKU choices they stay far above 10/hour. The reference and receipt numbers
move by one reference against the diag; the media-time attachment change is not in this baseline.

## Artifact files (sha256, first 16)

| File | sha256 |
| --- | --- |
| `rescore-base-1029-194618.json` (run file + score) | `5e1799a0dba96c0c` |
| `rescore-base-1029-194618-scenarios.json` | `b777d839a25b411e` |
| `rescore-base-1029-194618-comments.jsonl` | `190cddb1a0c9b1a7` |
| `rescore-base-1029-194618-metrics.json` (derived table above) | `6e6067ec0da28579` |

The observation path inside the run file is reduced to `<outside-git>/…`; the hash is unchanged.
