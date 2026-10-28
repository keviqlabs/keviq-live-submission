# SKU ambiguity and uncertain identity baseline

## Recorded-input extension

The same runtime baseline `650fe39d73e48560187803da699088b3dfceb1d8` now has **1 failed, 19 passed in 0.14 s**.
The failure is intentional acceptance evidence, not a passing suite or a runtime fix. Keep this PR draft as
the test foundation for the identity implementation; do not merge it independently. Run under the shared lock:

```sh
MODEL_API_KEY= uv run python -m tests.db_lock -- pytest -q -s tests/test_sku_ambiguity.py --tb=short
```

The selected metadata was independently read from canonical `events` with `transaction_read_only=on`.
The recording remains unchanged. At frame 58 s, `product_seen_9957806a909e4495b603` reports `sneaker / trắng`.
At frame 96 s, `product_seen_e0fd770dcde34b6f80fb` reports `sandal / trắng`. The latter is in the evaluation
interval labelled SP03; it does not report an SP03 SKU. The unchanged catalog lists both categories for
SP02 and SP03, but SP02's variants are white while SP03's are grey. `match()` therefore returns SP02.
`on_product_seen` selects its existing non-retired card at lines 219–221 and takes the `same` branch at
226–228: `card-9957806a909e` v4 becomes v5, with new frame provenance and the old identity and price.
The stored consequence is `card_a74a428fefec4483b2ea`.

The test feeds those two observed category/colour/frame combinations into the real card handler through the
in-process app. It establishes the recorded precondition, a live priced SP02 v4, with metadata-only fixture
versions. It retains the full committed C5 catalog throughout; it neither imports gold into the handler nor
changes the catalog. Speech processing, image production and intermediate media effects are not replayed.
Confidence 0.99 is a synthetic envelope value, unused by this handler, not a measured confidence claim.

The baseline reproduces exactly the identity/version error: SP02 v5 at 320000, not a separate unknown draft.
The assertion fails on equal old/new card IDs, with no setup error. The expected fix must preserve SP02 v1–v4
and open a distinct draft with null SKU, price and image and no inherited variants or stock. All 19 earlier
controls remain green. No model client is allowed; the test uses MemoryStore, while the common database lock
is held to respect shared test ownership. No database rows or running application processes are modified.

See [design](../../specs/uncertain-item-identity.md), [source trace](sku-identity-collision.json) and
[extension hashes and result](sku-identity-collision-red.json). No worker, app, measure, prompt, catalog or
contract file is changed. Cost is 0 USD; independent review is pending.

## Original baseline (preserved)

Baseline main: `650fe39d73e48560187803da699088b3dfceb1d8`. On an isolated checkout, **19 passed in 0.11 s;
0 failed**. Reproduce with:

```sh
MODEL_API_KEY= uv run pytest -q -s tests/test_sku_ambiguity.py --tb=short
```

The existing matcher abstains when a category and colour select more than one C5 SKU, regardless of catalog
order. The C5 catalog has no same-exact-kind/colour pair; `other` plus black collides SP07 with SP09, and
`other` plus white collides SP08 with SP10. The suite covers both collisions, missing colours, six unique
controls, one synthetic same-kind collision and the actual card handler with each of four prior SKUs.

In the four handler cases a test fixture first establishes a known prior card and the actual seller-price route
edits it. The full C5 catalog is restored before the ambiguous observation. The prior card remains unchanged,
and a separate draft opens with null SKU and price. This establishes only matcher/card behavior under the
synthetic observation. The fixture does not show that vision identified a product or that the order flow sends
ambiguity to a seller. `NoModel` fails if any handler requests a model client; the tests use the in-memory
store and in-process app, with no database fixture or model call (0 USD).

The [specification](../../specs/sku-ambiguity-acceptance.md) describes the missing distinguishing evidence and
the existing contracts-owner process. [Structured evidence](sku-ambiguity.json) retains the synthetic card IDs,
case outcomes and hashes. The initial test run exposed a test-harness `KeyError` on an optional field; the fixed
source is what was executed and recorded here. No runtime or catalog file is changed. Independent review remains
pending.

Test file SHA-256: `499719a0b4a4a45560254912cc01ab9fa0484505804c902b95553cfc853df245`.
Raw baseline log SHA-256: `407f9f1cc85aa379f389301b73a7fbfef75a97e7e2920af2d317f4ebc3532c18`.
