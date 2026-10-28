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
