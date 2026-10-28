# SKU ambiguity baseline

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
