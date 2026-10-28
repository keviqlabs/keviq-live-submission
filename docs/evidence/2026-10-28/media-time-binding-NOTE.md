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

Test file SHA-256: `baa14d4f33e93fecc56842082eefc7cf930ea2484a596bec8026552b5316daa2`.
Raw baseline log SHA-256: `973ecefcab6de961638b8ac218dc69092019f1f5c4f1b95d82e69ef801ba5a38`.
[Structured event evidence](media-time-binding-red.json) retains event IDs and versions without raw input text.
Lint and formatting passed. Independent implementation/review is pending.
