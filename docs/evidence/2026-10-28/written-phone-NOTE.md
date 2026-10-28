# Written phone acceptance baseline

Baseline main: `650fe39d73e48560187803da699088b3dfceb1d8`. The same test source was copied into an isolated
checkout of that revision and run against the existing `scan()` implementation: **39 failed, 514 passed in
0.25 s**, with no setup errors. All failures are ordinary assertions. Reproduction from the baseline checkout:

```sh
MODEL_API_KEY= uv run pytest -q tests/test_written_phone_shield.py --tb=short
```

| Group | Total | Passed | Failed | Baseline behavior |
| --- | ---: | ---: | ---: | --- |
| All committed C5 rows | 500 | 495 | 5 | Misses all five written-number positives; finds the other 15 positive rows |
| Derived written/mixed variants | 30 | 0 | 30 | Misses dots, mixed chunks, mixed dots, uppercase, NFD and international forms |
| Extra non-phone controls | 22 | 18 | 4 | Extracts a phone from two alphanumeric identifiers and from a suffix of two overlong separated runs |
| Fixed fixture counts | 1 | 1 | 0 | 500 rows, 20 positive flags, five fixed written source IDs, 30 variants |

The five missed C5 IDs are `c5-1-012`, `c5-2-011`, `c5-3-012`, `c5-4-013`, `c5-5-013`. Variant membership is
fixed from those source IDs, independent of the production regular expression. Expected results use the
committed `has_phone` flags. The four extra false positives are `color-code`, `sku-code`, `overflow-spaces`
and `overflow-dots`. The [structured results](written-phone-red.json) contain every case ID, expected/actual
signal and source hashes without duplicating comment text.

The [normalization proposal](../../specs/written-phone-normalization.md) specifies whole digit-word tokens,
Unicode normalization, bounded maximal candidates and structural phone validation. The draft changes no
shield implementation, runtime, event schema, gold flag or fixture source. Tests use no database fixture,
model client or network transport: 0 USD. This is shield recognition evidence, not comment-hide or latency
E2E evidence. The later implementation must run the existing label/shield suite too.

This draft intentionally makes the acceptance tests red and must not be merged alone. Independent review
and the shield implementation remain pending. Lint and formatting pass.

Test file SHA-256: `b9080bf05ea8b495135263134766b1fc56c7bc2e8ef659e74a6644e6a9b25d45`.
Raw baseline log SHA-256: `369250d7a2bb881ebd8aa3eba333d53c96d6b5532ca20ce7b7e5b4564398bd88`.
