# Media-time binding acceptance baseline

Baseline main: `650fe39d73e48560187803da699088b3dfceb1d8`. Four ordinary assertion failures in 6.07 s,
not setup errors or missing-function placeholders. The input/output specification is
[media-time-binding-acceptance.md](../../specs/media-time-binding-acceptance.md).

The same test file was copied into an isolated checkout of that main revision and executed through the shared
`.scratch/keviq_test.lock` command wrapper on `keviq_test`. Frame recognition and classification are synthetic;
app, runner, card, label, order and seller-price handlers are real. Model API calls: 0.

| Case | Actual baseline consequence | Required consequence |
| --- | --- | --- |
| Switch before recognition | A order confirmed at 150000 before B price exists | Wait for B approved version at 200000 |
| Price before card | A receives 150000, but the early order remains on P at 120000 in NEEDS_SELLER | Resume order on A’s later approved version |
| Late result / A reappears | Stale guard rejects the old result, but current/order still selects B | A remains current at 150000 even after B’s edit |
| Incomplete evidence expires | A is silently confirmed | NEEDS_SELLER with exactly two candidates, no confirmation or customer item-selection question |

The two expected-failure probes from #47 are removed in this draft’s tree and consolidated into the full
four-scenario regression. Historical #47 evidence is unchanged. Candidate fields remain gated by the existing
contracts-owner process; no production implementation or schema is changed. The draft intentionally makes
`make check` red until the reviewed binding implementation is applied; it must not be merged alone.

Previous four-scenario test file SHA-256: `e4bbb08f571a996519d95234dfeb669de01c62ec024e89327ca299fc3b2a372e`.
Executed baseline test file SHA-256: `baa14d4f33e93fecc56842082eefc7cf930ea2484a596bec8026552b5316daa2`.
Raw baseline log SHA-256: `973ecefcab6de961638b8ac218dc69092019f1f5c4f1b95d82e69ef801ba5a38`.
[Structured event evidence](media-time-binding-red.json) retains event IDs and versions without raw input text.
Lint and formatting passed. Independent implementation/review is pending.

Wording edited, no behavior change: the module docstring and specification now cite rule 1 of
`docs/specs/current-item-and-stock-rules.md`. The Python AST is unchanged after excluding the module
docstring. Tests were not rerun for this wording-only update; the four failures and log hash above remain
the original executed baseline, whose source hash is preserved separately.

## Blocked-frame deadline extension

The additional `test_expiry_deadline_while_lower_frame_result_is_blocked` holds the lower frame's recognition result on an explicit event. A newer observation is delivered while that result stays blocked. The seller outcome must be observed between comment ingestion + 4000 ms and that deadline + 200 ms scheduling tolerance (20 ms wall time at 10x compression), with both event timestamp and ingestion timestamp in that window, before the release event is set. Early orders and customer confirmation/item-selection messages remain forbidden. The gate is released only in finally after the snapshot/assertions; the worker timeout is later than the checked window.

Baseline-identical runtime at 650fe39: **0 passed, 5 failed in 7.14 s, rc 1**, shared keviq_test lock, blank key, zero model calls. The new case fails because no NEEDS_SELLER outcome exists while the frame remains blocked; actual states are DRAFT/CONFIRM_SENT. This is an assertion failure, not a setup failure. Initial invocation without database configuration skipped all five cases; it is preserved outside Git and not counted as evidence. No existing run artifact was replaced.

Current test SHA-256: `711652f8a0b1d706394d92458d118aa96f6d9bd0a682cc6df9ae1c903583f2ad`. Raw extension log SHA-256: `6c6a4926f9b2b39baba8e0ee958ed689e8170eb74a3f01fd9dfea7cefdf288de`. See [structured extension evidence](media-time-deadline-red.json). The original four-case source/log hashes above remain historical. No runtime, catalog, schema or gold changes.

## Canonical frame inputs and after-frame fence

The price-before-card timeline now waits for the runner to release frame 11, then records canonical frame 14. Its media time is greater than the comment's time, so it supplies the after-frame fence. In this execution frame 11 completed at 1793197476033; frame 14 was ingested at 1793197476181, before comment ingestion + 4000 ms (1793197477224) by 1043 logical ms. Frame 14 therefore cannot replace the queued frame 11.

Both expiry timelines now record canonical frame 20 before the comment at 20.4. The separately injected B recognition at 23 cites that existing frame, while lower frame 19 is still pending or gated. The bounded-deadline observation still records `result_released: false` and `result_completed: false`; the gate is released only in the existing finally block. Snapshot frame outcomes recorded after that observation do not imply the gate was open during the deadline check.

All 25 scenario assertions, the 4000-ms bound, the 200-ms tolerance, prices, candidates, states and no-message expectations are unchanged. The fixture records actual frame releases, canonical frames, comments and recognition references; no readiness or order behavior is implemented. Runtime trees remain identical to `650fe39d73e48560187803da699088b3dfceb1d8`.

Command: `MODEL_API_KEY= uv run python -m tests.db_lock -- pytest -q -s tests/test_media_time_binding.py --tb=short`, with both database variables targeting `keviq_test`, the shared lock held, and structured snapshot output enabled. Result: **0 passed, 5 ordinary assertion failures in 7.17 s, 0 skipped, rc 1**, zero model calls. Full `make check` was not rerun for this intentionally red foundation. Lint and formatting passed. No green runtime or paid-model result is claimed.

Current test SHA-256: `206d49fff3ba24a5db59a4c80a894af89e88e6ea2dabde139a8a5eb227a5f96a`. Raw corrected-run log SHA-256: `4ab0b89bfe345d735550d3e122fe4b10e711e1c2ceebcda40f43b26472dd7669`. Structured snapshot SHA-256: `cc97547599f07f73a45ebdae896a21df29d40dc6afab398537722bea9d8f0add`. [Corrected input evidence](media-time-fixture-alignment-red.json) includes frame/comment timestamps, existing source references and the blocked deadline snapshot. Earlier source/log hashes remain historical.

## Immutable prior-card history predicate

The prior-card setup creates a real version-1 draft with null price, then appends the seller-approved 120000 version. Requiring every historical price to be 120000 contradicted that append-only setup. The corrected test captures the complete prior-card event history immediately after price approval, verifies the null-price draft and actual seller-approved version, then compares the complete final history with that snapshot. No version or payload field is omitted; the event accessor is unchanged.

Only this predicate is replaced, with two setup witness assertions added. The other 24 original assertions and every source input, binding check, 150000 price/version pin, deadline and clock tolerance are unchanged. Source normalization outside the approved snapshot/predicate change matches the previous foundation.

On baseline-identical runtime `650fe39d73e48560187803da699088b3dfceb1d8`, the focused command produced **0 passed, 5 ordinary assertion failures in 7.10 s, 0 skipped, rc 1**, with zero model calls. The raw log also contains a store-cancellation/reconnect teardown warning. The price-before-card case passed the new setup witnesses and still failed its earlier binding assertion; the final full-history comparison was not reached. This is not independent green runtime evidence. Full `make check` was NOT_RUN for this intentionally red foundation.

Current test SHA-256: `b9d352c7d0803efe89812d04ecdeec111ea5d97e27439078bf4bb78f917cdd45`. Raw focused log SHA-256: `44422c61b54b458f8fefccf36673a381d06d5f70f0308f98a8c0e2150c0a4af1`. Prior source/log hashes above remain historical. No runtime, contracts, catalog, gold, criteria or price authority changed.
