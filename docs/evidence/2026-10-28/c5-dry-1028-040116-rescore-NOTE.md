# Scope of the C5 dry run c5-dry-1028-040116, rescored

What this is: the dry session `c5-dry-1028-040116` scored again with the scorer in this PR after the review ruling of
04:18 VN on price proposals. Nothing ran again; the stored events of the session were read from `keviq_test`. It is
not the C5 measurement: the 5-loop live run with real vision and real speech has not run. Every number below is a
dry-run number.

The run's own files and NOTE (`c5-dry-1028-040116*`, NOTE sha256
`d66d96f73587d71c574cc282634bba96fd20b07b70d3dfb6b8d99248df4cc18f`) are unchanged. Where this NOTE and that one
differ, this one holds for the proposal section and the with/without estimate; every other number is the same.

## Rescore

- Command: `MODEL_API_KEY= uv run python -m measure.c5 --rescore docs/evidence/2026-10-28/c5-dry-1028-040116.json
  --out docs/evidence/2026-10-28`, rc 0, `events 465`, `ledger rows 0`.
- Scorer: `measure/c5_score.py` sha256 `e3ddd38a4556764b6bd5d720b10e8b4ec4f010752a4dc815f0b0d41a3816bfd6`; the
  other measure files are unchanged from the run commit `9e09829`. Tests: `tests/test_c5_proposals.py`.
- Files: `c5-dry-1028-040116-rescore.json` `1b8c99360162c0a1e0af0e0a95e5fa9bf2eec5006627b174ab74e00a59150dbf`,
  `-rescore-scenarios.json` `1a565e1e06174619a67fc2b0e5eb6e4b6576cfa001286293036398ca18f6bdd6`,
  `-rescore-comments.jsonl` `c806b561a36fd59d014b2c09b7978c7e5d35f3eff3815fdaecb919f278a85e46` (the same bytes as
  the run's).

What changed against the run's score, value by value:

- `proposals.handling_ms`: median 4052, n 4 → median 4066, n 3.
- New `proposals.handling_ms_all`: median 4052, n 4 (the run's line, kept for audit).
- New `proposals.stale`: n 1, `price_proposal_c12f090722b94a4882f1`, reason "closed by a version ingested before
  the proposal".
- New per row: `stale`, `right_price`. New `proposals.wrong.card` 1 and `proposals.wrong.price` 5.
- `estimate.assumptions.tap_s_used`: 4.052 → 4.066. The rounded shop minutes stay 1.5.
- Each situation's `code.status`: PASS → NOT_RUN. A rescore does not run the per-situation test files; the run's
  PASS stands for those.

## Stale on arrival

A proposal is stale on arrival when the version that closes it under D-024 was ingested before the proposal
itself: the shop had already moved the card on, so there was nothing left to act on. Its raw handling time stays in
its row and in `handling_ms_all`; it is counted apart and kept out of `handling_ms`. Nothing is clamped to 0 and no
row is removed.

One case, `c12f`, confirmed in the review (PR #31) from the stored events:

| event | seq | ingested_ms | card version | price |
|---|---|---|---|---|
| proposal `1b4e` | 18236 | 1793134928398 | 4 | current 250000, heard 230000 |
| card `card_ece5851…`, the shop types a price for `1b4e` | 18238 | 1793134932436 | 5 | 220000 |
| proposal `c12f` | 18239 | 1793134932485 | 4 | current 250000, heard 220100 |

`c12f` was emitted against card version 4, 1 seq and 49 ms after version 5 was stored. D-024 closes it at version
5 (`fixed`, -49 ms). Its outcome is kept as the rule gives it.

- Handling median over the actionable proposals: 4066 ms, n 3 (4038, 4066, 4078).
- Over every timed proposal, as in the run: 4052 ms, n 4.

Morning backlog, not changed tonight: the worker emits a proposal against the card version it holds in memory and
does not reread the stored version first, so it can emit one that is already stale. The stale count is a
diagnostic for that. Runtime code is unchanged.

## Wrong proposals by source

The proposal accuracy (right card, right price, against gold and the price window) is independent of what the shop
did. 6 of 8 proposals are wrong: 1 on the card, 5 on the price. The source of each is taken from the trace of its
speech line in `probe/voice.json` (local test1, not committed; only numbers and number words here).

| proposal | voice row | heard | wrong on | source |
|---|---|---|---|---|
| `1b4e` | 8 | 230000 | price | the number run in the line carries "mươi" twice; the reader adds both |
| `c12f` | 9 | 220100 | price | reader: "nghìn một" before a classifier is read as one more hundred (`worker/voice/vn.py:96-97`) |
| `9b0f` | 18 | 280000 | card | the price is right for the held item SP03; the card is SP02's |
| `3a7b` | 31 | 160100 | price | reader: "nghìn một" before a classifier (`vn.py:96-97`) |
| `b271` | 55 | 600100 | price | reader: "nghìn một" before a classifier (`vn.py:96-97`) |
| `13ad` | 62 | 250000 | price | read as said; the local test1 gold (`heard_vs_script`) records that at 334.9 s the SP01 price heard differs from the script price 220.000 |

- Reader rule (3): `c12f`, `3a7b`, `b271`. In test1, these are the only 3 lines with a unit, then "một", then a
  word that is not a number.
- Number word repeated in the line (1): `1b4e`.
- Heard differs from the script, noted in gold (1): `13ad`.
- Card (1): `9b0f`.
- Injected by the dry script: 0. The dry runner posts each test1 line's text unchanged (`measure/c5_dry.py:7`,
  `:106-111`), and nothing in it shifts a price.

The 3 reader cases are a runtime defect of the rule price reader, for the morning backlog. The other 3 are not read
as reader errors. Neither the outcome (fixed, dropped, replaced) nor a shop action is taken as proof that a
proposal is wrong. Gold is unchanged.

## Caveats

- The caveats of the run's NOTE hold.
- `c12f` stays in the proposal count (8) and in situation 2; only the handling median leaves it out.
- The 22 interventions in 6.32 minutes (208.9/h) come from the scripted shop, not from a real host.

wording edited 11:03: internal labels removed; numbers unchanged. The NOTE sha256 d66d96f7… above is the one before
that wording edit, in both NOTEs; after it, c5-dry-1028-040116-NOTE.md is a01bd69c1aa9e7f660a41e1c619c1c61b13c279377fd1b87e55fe0042b1918ea.
