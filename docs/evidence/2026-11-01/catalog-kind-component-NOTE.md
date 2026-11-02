# Catalog kind component check — 1 November 2026

This local candidate preserves coarse categories and uses explicit kind/color evidence only for
`other` items. It removes one information bottleneck; it has **not** demonstrated more completed
orders, a C5 pass, or production latency. Base: `e576b4a8aa672b9a3bfe4c9d785347e49a9d5004`.
D-035 records the narrow extension of D-026. This note is author evidence, not an independent verdict.

## Behavior and boundary

The active catalog supplies kind/color vocabulary, never model SKU/price authority. Exactly one
clear, confident kind/color match can open a new unpriced draft card. Unclear, low-confidence,
missing, unknown, mismatched or ambiguous evidence creates/retains unresolved identity. Clear
recognition after an unresolved card opens a new card; it never changes SKU in place or restores
an older card's price. Repeated observations include kind/state in their identity comparison.

Catalogs without explicit `other` kinds retain the original vision prompt/schema. Legacy events
with both optional attributes absent preserve coarse unique matching. If either new attribute is
present, missing/null/unclear evidence stays unresolved, even with one catalog item.
Non-`other` handling, speech prompts/keywords, price authority, variants, schemas, rules, gold and
catalog bytes are unchanged. This candidate does not solve the existing no-product transition
behavior. A catalog match is evidence of identity, not seller confirmation of the prediction.

## Reproduction and test results

No model calls were made. Focused memory-store tests use fake observations and reject a model
client. On the base implementation, the initial focused tests returned `12 failed, 1 passed`
(rc 1). After the implementation and one additional compatibility case, this command returned
`44 passed in 1.91s` (rc 0):

```sh
DATABASE_URL= TEST_DATABASE_URL= MODEL_API_KEY= python -m pytest -q -p no:cacheprovider   tests/test_catalog_item_kind.py tests/test_identity_continuity.py   tests/test_catalog_path.py tests/test_vision.py
```

Direct regressions returned `82 passed in 1.69s` (rc 0):

```sh
DATABASE_URL= TEST_DATABASE_URL= MODEL_API_KEY= python -m pytest -q -p no:cacheprovider   tests/test_card.py tests/test_card_handoff.py tests/test_card_window.py   tests/test_orders_ok_after_card_change.py tests/test_attachment_reference_shapes.py   tests/test_rules_invariants.py
```

The full `make check` result belongs in the handoff and PR at the final commit. It is not inferred
from these focused checks and must be green before push. The final integrated tree also needs review.

## Saved-output adapter (all 70 frames and all 28 named-item comments)

`tests/catalog_kind_replay.py` is a no-network component helper. Supply the frozen comparison
outputs from PR #83 as `COMPARE_EVIDENCE`; source hashes are below. For example, after that
comparison evidence is present, use `COMPARE_EVIDENCE=docs/evidence/2026-11-01`.

```sh
DATABASE_URL= TEST_DATABASE_URL= MODEL_API_KEY= python -m tests.catalog_kind_replay   --frozen "$COMPARE_EVIDENCE" --mode A --output /tmp/catalog-kind-legacy.json
DATABASE_URL= TEST_DATABASE_URL= MODEL_API_KEY= python -m tests.catalog_kind_replay   --frozen "$COMPARE_EVIDENCE" --mode kind --output /tmp/catalog-kind-adapter.json
```

For the base comparison, run the same helper with `PYTHONPATH` pointing to a clean base checkout
and invoke it by file path rather than `-m`. It imports the base worker/app; it does not modify
that checkout. All three runs returned rc 0. Base A and candidate A output JSON matched exactly.

- A uses all saved minimal-effort coarse observations from the comparison, without changing them.
- The kind adapter uses the **same A outputs**, adding B0 kind/color/state/confidence **only where
  A says `other`**. This synthetic combination is not an output from the new combined prompt.
- Every saved frame is passed through the real vision event producer and card handler with an
  in-memory app. Frame match is the observation-to-catalog mapping, even for suppressed repeats.
- All 28 comments retain the actual reference kinds saved in the 0742 run; gold is used only for
  scoring. The real current/previous selector and reference resolver run on the synthetic cards.
- No price/speech is injected. Every card remains unpriced. No order, message, price proposal or
  model-call event is produced. No attachment deadline, readiness, billing or end-to-end timing
  is simulated. This is not the earlier 15/28 order-linked result, nor the proxy score in #85.

| Component | Base A | Candidate A | Candidate kind adapter |
|---|---:|---:|---:|
| All 70 frames: right / wrong / no SKU | 26 / 7 / 37 | 26 / 7 / 37 | 45 / 7 / 18 |
| 20 same-color-other frames: right / wrong / no SKU | 0 / 0 / 20 | 0 / 0 / 20 | 15 / 0 / 5 |
| 28 named-item references: right / wrong / no SKU | 17 / 0 / 11 | 17 / 0 / 11 | 21 / 0 / 7 |
| Card events | 27 | 27 | 27 |
| Model calls / orders | 0 / 0 | 0 / 0 | 0 / 0 |

Here `no SKU` includes abstention and no-product observations; it is not automatically an error
or a passed safety criterion. All seven wrong frames remain SP03 predicted as SP02, at 97, 102,
103, 107, 109, 112 and 114 seconds. None were excluded or relabeled.

## Complete comment accounting

All selected cards have `price=null`. “Right” below means the selected SKU matches the unchanged
gold, not a successful order. Saved reference kinds are inputs; product gold is not.

| Comment | Saved reference | Base selected SKU | Adapter selected SKU | Adapter result |
|---|---|---|---|---|
| c5-1-006 | current_card | SP01 | SP01 | right |
| c5-1-010 | current_card | SP01 | SP01 | right |
| c5-1-016 | current_card | SP02 | SP02 | right |
| c5-1-018 | previous_card | SP01 | SP01 | right |
| c5-1-021 | current_card | SP02 | SP02 | right |
| c5-1-024 | current_card | SP02 | SP02 | right |
| c5-1-029 | current_card | none | none | none |
| c5-1-031 | previous_card | SP02 | SP02 | right |
| c5-1-032 | current_card | none | none | none |
| c5-1-035 | current_card | none | none | none |
| c5-1-041 | current_card | none | none | none |
| c5-1-043 | current_card | SP04 | SP04 | right |
| c5-1-045 | previous_card | none | none | none |
| c5-1-047 | current_card | SP04 | SP04 | right |
| c5-1-048 | current_card | SP04 | SP04 | right |
| c5-1-054 | current_card | SP05 | SP05 | right |
| c5-1-055 | previous_card | SP04 | SP04 | right |
| c5-1-056 | current_card | SP05 | SP05 | right |
| c5-1-060 | current_card | SP06 | SP06 | right |
| c5-1-062 | current_card | SP06 | SP06 | right |
| c5-1-071 | current_card | none | SP07 | right |
| c5-1-074 | current_card | none | SP07 | right |
| c5-1-078 | current_card | none | SP08 | right |
| c5-1-082 | current_card | none | none | none |
| c5-1-085 | current_card | none | none | none |
| c5-1-091 | current_card | none | SP10 | right |
| c5-1-095 | current_card | SP01 | SP01 | right |
| c5-1-098 | current_card | SP01 | SP01 | right |

Four additional references match (071, 074, 078, 091). Two of the six `other` cases (082, 085)
still have mismatched blue observations against the black catalog item and remain unresolved;
no color is forced to the catalog. Case 031 still selects SP02 through `previous_card`. Case 045
remains unresolved. No previously correct reference becomes incorrect in this component check.

The previous-card snapshot does change where previously unidentified items now have identities:
078 has previous SP07; 082/085 and 091 have previous SP09; 095/098 have previous SP10. These
comments use `current_card`, so their saved references do not select those previous cards. This
does not prove behavior for new previous-item utterances. Unresolved cards are not skipped to
manufacture a better previous reference; all original current/previous semantics are retained.

## Remaining limits

- Seven SP03 wrong visual mappings remain; the four associated current-item references remain
  unresolved. This patch does not repair the visual/color error or missing size.
- SP09 mismatched color and spoken-price/catalog-price differences remain. No catalog/gold edit
  or catalog-price fallback is used. SP01 quantity/stock and later-price limits remain.
- Extra correct component references do not imply four extra orders: real price, variant,
  reference timing, attachment readiness, seller approval and customer confirmation still apply.
- The sparse 70-frame sample and synthetic arrival order cannot prove real cadence, read
  completion, cancellation, model quality, C1 timing or C5. The changed prompt needs a separately
  authorized runtime measurement after review, with all original denominators and failures kept.
- The current no-product path still emits no new observation; general transition safety is not
  claimed. Recognition cannot edit an existing unresolved card's SKU, and no new state/API was added.

## Frozen source checksums

| Source | SHA256 |
|---|---|
| `model-compare-calls.jsonl` | `ae4ad8d8dc45250b86efaf8d21c2c99e66959dea89ccb36f968e435948a10ec0` |
| `model-compare-score.json` | `673fbef35545e989626924b1fc919a59efb6deb90b29f5ee5327b2db224f4175` |
| `identity-1101-paid-0742-events.jsonl` | `5255380e49fa29ca7faace83eb20247e8b853bdc98a6d562758b6d41ad8e0214` |
| `stream-1.jsonl` | `9c259c030cda0e74a10b6bf725aededdb741d874a3c63ff6653e38c9edb9e79c` |
| `catalog.json` | `fd5e86ad67ada1c0d200386fedd642e688440a34970e795d65b99ebc267adc73` |

## Repair checks after the first full suite

The initial candidate at `d66be0540ffeb5153202d2c8d29bef7820f46ec9` did not pass the full gate:
`make check` returned rc 2, with `4 failed, 1556 passed, 7 skipped in 866.99s`.
All four failures were the original one-item legacy setup in `tests/test_sku_ambiguity.py`;
no timing failure was reported. That existing test file is unchanged in this repair.

A separate review reproduced three sequences where an explicit controller price could reach a
new same-color keyboard card because both still have coarse category `other`. The repair retains
the existing media-time candidate selection and adds a price gate for refined identities: exactly
the card's explicit kind must be named, with no conflicting kind. Unknown identity, unnamed/generic
item language and conflicting/multiple kinds leave the price unresolved. A bare "this is 450k"
therefore waits for seller input on a refined card. This can reduce automatic price coverage;
no end-to-end gain is claimed. Existing legacy-card speech decisions and prompts are unchanged.

New deterministic tests plus the unchanged ambiguity file returned `11 failed, 37 passed`
before the repair (rc 1). After repair, this focused command returned `160 passed in 3.44s` (rc 0):

```sh
DATABASE_URL= TEST_DATABASE_URL= MODEL_API_KEY= python -m pytest -q -p no:cacheprovider \
  tests/test_catalog_item_kind.py tests/test_sku_ambiguity.py tests/test_identity_continuity.py \
  tests/test_catalog_path.py tests/test_vision.py tests/test_card.py tests/test_card_handoff.py \
  tests/test_card_window.py tests/test_orders_ok_after_card_change.py \
  tests/test_attachment_reference_shapes.py tests/test_rules_invariants.py
```

The three wrong-kind arrival/media-time cases are blocked. Matching-kind pricing still opens
the matching card; the already-priced previous-card ambiguity control still waits. Pending speech
across an item switch cannot price the new wrong kind. True legacy absence matches the original
coarse path, while explicitly null/unclear/partial typed attributes cannot take that fallback.
The complete A and kind replay JSON results remain byte-identical to the original component
outputs above. There is still no speech injected in that adapter, so this equality does not
measure the new price gate's effect on real throughput. Final full-suite and verification results
remain separate handoff/PR gates at the repaired head.
