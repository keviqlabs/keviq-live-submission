# Diagnostic — diag-test1x1-1028-1531

| Objective | Verdict | Measured result |
| --- | --- | --- |
| Every card opens within 10 s of its source frame | PASS | Version 1 of all 9 cards: p50 3.687 s, max 4.616 s; 9/9 at most 10 s. |
| Every frame has an outcome | PASS | 351 frames; SETTLED; pending = running = queued = 0; done_seq = head = 11364. Skipped 205, stale 0. Failed lower bound 1, upper bound 1; bounds, not a lane-counter export. |
| Digit-phone first hide within 5 s by stored ingestion time | PASS | 3/3 cases; max 0.028 s (individual delays 0.012 / 0.017 / 0.028 s). |
| Loop does not end INCOMPLETE | PASS | One full loop; both end barriers SETTLED; runner exit 0. |

These are diagnostic targets. The SKU/reference quality failures below remain failures and are outside these four targets.
Version-1 latency starts at each card's source frame, not at the gold item's first appearance or at its first live/priced version.
First-live latency is reported separately: p50 14.620 s, max 30.335 s (n=9).

## Execution and isolation

- Revision: `650fe39d73e48560187803da699088b3dfceb1d8`; tree `8bd42cdde063d902077da1ce8c54a92171b6e4e5`.
- One paid test1 loop, `measure.c5 --base-url http://127.0.0.1:8000 --loops 1 --paid --run diag-test1x1-1028-1531`.
- Started 15:31:03, finished 15:37:44 VN on 28 October 2026. Cap 2.00 / warning 1.50 in app and runner; worker/media proofs match the revision, catalog, key-present flag and minimal effort.
- Seven preconditions passed, including a refreshed app PID 97939, correct operator checkout, healthz 200, port 8001 absent, empty session cost and shared test-lock ownership.
- The shared test lock was held by a separate process; the paid runner was not wrapped in the blank-key test wrapper.
- Test DB connections at acquisition / after completion: 0 / 0. The lock was released. No concurrent test, probe or E2E was observed; the runner's own final code checks all returned 0.
- Runner failure-log counter 0. Final progress: delivered=finished=1096, errors=1, skipped=205, stale=0, pending=running=background=queued=0.
- Frame outcome bounds: failed <= final progress errors = 1; failed >= 351 - 205 - 0 - 145 successful product_seen model_call rows = 1. These are bounds, not exact per-lane counters. The number of product_seen events is never used to infer done frames.
- One product_seen call was cancelled; ledger v2 records it as not_ok with cost 0. The provider may still bill a cancelled call.
- Image rule: IMAGE_OK at 15:31:59, about 55 s after the first frame; card_cutout ok plus card-e5994acb61f5 v5 with a valid data image. Guard exit 0. The 1 USD early-stop threshold was never reached.

Offline precondition: `e2e-1028-1524` on the same revision, runner exit 0, SETTLED/SETTLED, runner_failures=0, SQL 0 model_call rows, API session_usd 0.0, worker/media C5PROC sha `650fe39d73e48560187803da699088b3dfceb1d8`. Trial port 8040 stopped, lock released, test DB connections 0/0. Four-step order/receipt verification NOT_RUN this time because orders/ is unchanged from 51e3b6d. The offline NOTE distinguishes 330 expected blank-key vision failures in progress from the runner failure-log counter; no model success is claimed.

## Per-item detections and card consequences

A detection belongs to an item by the scorer's existing gold media span; this is not an independent SKU-recognition verdict.
The first time column is source-frame time minus the gold change mark. The second is detection ingestion minus that mark.
Negative source deltas lie within the approved change band and are retained.

| Item | First product_seen | Source minus gold, s | Ingest minus gold, s | Card receiving the detection | Why |
| --- | --- | --- | --- | --- | --- |
| SP01 | product_seen_e5994acb61f548dfa094 | 9.2 | 13.136 | card-e5994acb61f5 v1 (SKU SP01) | new version 1 |
| SP02 | product_seen_9957806a909e4495b603 | -0.6 | 3.199 | card-9957806a909e v1 (SKU SP02) | new version 1 |
| SP03 | product_seen_e0fd770dcde34b6f80fb | 3.1 | 6.639 | card-9957806a909e v5 (SKU SP02) | reused SP02: sandal/white matched its catalog categories and color |
| SP04 | product_seen_bc094ad23d0843ba8871 | 10.1 | 12.618 | card-bc094ad23d08 v1 (SKU SP04) | new version 1 |
| SP05 | product_seen_c5b8549fde6d4f8ba502 | -0.3 | 3.331 | card-c5b8549fde6d v1 (SKU SP05) | new version 1 |
| SP06 | product_seen_39fa182ce480487f90c2 | 0.5 | 3.865 | card-39fa182ce480 v1 (SKU SP06) | new version 1 |
| SP07 | product_seen_275d3ef551eb4555b7f6 | 3.9 | 6.741 | card-275d3ef551eb v1 (SKU none) | new version 1 |
| SP08 | product_seen_03e5cce2e0f74daa95fa | 0.3 | 2.474 | card-03e5cce2e0f7 v1 (SKU none) | new version 1 |
| SP09 | product_seen_e16f7dbe7caf48c880bc | 6.5 | 11.063 | card-e16f7dbe7caf v1 (SKU none) | new version 1 |
| SP10 | product_seen_10e014cb322c4b8ba2f9 | -3.3 | 0.439 | card-10e014cb322c v1 (SKU none) | new version 1 |
| SP01 (return) | product_seen_88016756fde84c68b226 | 7.2 | 10.372 | card-e5994acb61f5 v6 (SKU SP01) | returned item updates its existing open card |

All ten first-appearance spans contain a detection; the return of SP01 also does. SP03 has no new card:
its detection reports sandal/white, then causes SP02 card-9957806a909e v5. Both catalog category lists admit sandal,
and the observed color matches the open SP02 SKU. This is a catalog/SKU reuse failure, not a stale or unseen event.
SP07–SP10 have cards with no SKU. Card count therefore does not establish correct SKU assignment.

## Quality results retained

- Item coverage by source span: 9/10, FAIL. Reference accuracy: 7/28 = 0.25, FAIL; a one-loop sample also remains below the full criterion's minimum 50 references.
- Order recall 1.0 on 30 order comments; 100 simulated comments total. This is not the full 500-comment C5 gate.
- Two receipts: pinned version correct 2/2; correct item against gold 0/2, FAIL.
- Scripted shop interventions: 19, 178.62/hour over 6.38 minutes. These are scripted actions, not a measurement of a human seller's workload.
- Proposals: 3; right card 2, right price 2, both 1; used 1, dropped 2; stale_on_arrival 0.
- Situations: PASS 2/6/7/9/12; FAIL 1/3/4/5; NOT_TRIGGERED 8/10; TEST_ONLY 11.
- The SKU failure belongs to the planned SKU repair. Pull requests #49 and #50 are its foundation; #50 is not merged separately. The SP03 regression and independent review remain required.

## Receipt image readback

The existing scorer labels image_path NOT_RUN because the stored-probe-only source_session/source_t fields are absent.
The separate read-only check finds PNG bytes in both real receipts, matches each image exactly to the cited card version,
and records their hashes below. This proves image/version attachment 2/2, not correct item identity or visual cut-out quality.
The original scorer output is preserved.

| Order | Cited card | PNG bytes | SHA-256 |
| --- | --- | --- | --- |
| o-c5-1-016 | card-e5994acb61f5 v5 | 43262 | `5e52130315944f33b469225f196e52b42a28d85146abfaadbee5513f55216e70` |
| o-c5-1-029 | card-9957806a909e v5 | 3714 | `9f15f3542c7c1f334ce6c6e277bb803ae4cfd54038ba24884ad4887a75072251` |

## Spend and Billing

| Purpose | Rows | Not ok | USD |
| --- | --- | --- | --- |
| card_cutout | 9 | 0 | 0.02250000 |
| card_from_speech | 61 | 0 | 0.15704850 |
| label_comments | 77 | 0 | 0.11264420 |
| product_seen | 146 | 1 | 0.20870955 |
| transcribe | 1 | 0 | 0.01755000 |
| Total | 294 | 1 | 0.51845225 |

Final API: session 0.518452; today 2.448411; total 9.801175 USD. Read-only ledger: 0.51845225 USD,
293 ok rows and 1 not-ok row, no session calls outside today's ingestion window. No amount was subtracted.
Billing screenshots reported by the operator: 15:20 → 15:41 VN, credit 146.70 → 146.18 USD, difference 0.52 USD;
balance 158,783 VND unchanged. This agrees with the ledger to two decimals; it does not prove the cancelled call was unbilled.

The 0.35–0.44 USD estimate was calibrated on the earlier run that opened only two cards per loop. This run costs
0.078452 USD above the upper estimate (17.83%). It completed 145 successful vision calls, 61 speech reads, 77 label calls
and 9 cut-outs. Against the old run's first loop, speech rose 0.074303 → 0.157049, labels 0.072379 → 0.112644,
and cut-outs 0.005 → 0.0225, while vision fell 0.274023 → 0.208710. Transcription stayed 0.01755.
The larger speech/label/cut-out totals account for the increase; this is one stochastic diagnostic, not a revised general rate.

## Reproduction and retained evidence

From the repository root, with the saved session available on the local app:

```sh
PYTHONPATH=. uv run python docs/evidence/2026-10-28/scripts/c5_timing.py --base-url http://127.0.0.1:8000 --run-json docs/evidence/2026-10-28/diag-test1x1-1028-1531.json
PYTHONPATH=. uv run python docs/evidence/2026-10-28/scripts/c5_ledger_sql_v2.py diag-test1x1-1028-1531
uv run python docs/evidence/2026-10-28/scripts/c5_perloop.py docs/evidence/2026-10-28/diag-test1x1-1028-1531.json
PYTHONPATH=. uv run python docs/evidence/2026-10-28/scripts/c5_diag_details.py --base-url http://127.0.0.1:8000 --run-json docs/evidence/2026-10-28/diag-test1x1-1028-1531.json --timing-json docs/evidence/2026-10-28/diag-test1x1-1028-1531-timing.json --ledger-json docs/evidence/2026-10-28/diag-test1x1-1028-1531-ledger-v2.json
```

All four read-only exports exited 0. The details export reproduces the previously collected counts, card latencies and digit-phone deltas.
An auxiliary display initially requested a nonexistent score key; only the display was corrected. No model or measured session was repeated.

Script SHA-256:
- `c5_timing.py`: `4617a5d8ef13149be9bc70b08ada90b0a9d0fd4bb63a9f3e1590dcbe6c7152be`
- `c5_diag_details.py`: `5bac82f2f1d0b5eb2096f1062c1860b684a459f14d7be23617acbe46a2c733eb`
- `c5_ledger_sql_v2.py`: `92ac6157c0aff1c90c46ef3a80ea9981e904eca786492acb5d9e134846e8b57a`
- `c5_perloop.py`: `f61db0ec3640f6c5a89de1aac389c9bd68ee6b9e502aef89dcb569fcc5bc279c`
- `paid-image-5min.py`: `69e49563c64f0784d74f0c6f87f2eb4c2a2a8188eb387a70d93d9a5dda440c53`

Integration reference: pull request #47 and `slow-vision-integration-NOTE.md`, SHA-256
`f55b022db2d2ff68c9b15617c4c447e7e905eb55f9b05552f5960f02fbd95e11`.
The previously accepted 1x test is not repeated. Source differences from 51e3b6d include nine merged PRs (#36, #40–#47), no migrations and no contracts changes.
Private logs, screenshots and original run files remain outside Git; their basenames and hashes are in `diag-test1x1-1028-1531-private-hashes.json`.
No new paid run is authorized. Per-lane progress instrumentation requires its own tested, independently reviewed PR before another diagnostic or full C5; keep it separate from deduplication changes.

## Delivery validation

`make check` exited 0: 526 passed, 2 expected failures; lint, formatting and quick fixture measurement passed. The quick fixture numbers are not diagnostic model results.
