# Preserve frame evidence across replay loops

The five-loop unpaid rehearsal completed its process schedule but lost automatic
attachment coverage after the first loop. It restarted the worker at each loop
while retaining one session and epoch. A new worker started at the existing event
head with an empty completion ledger, while the server correctly required all
earlier frames of the session. Keeping product cards and prices did not supply the
missing frame completions.

The live replay runner now starts one worker before the first media loop and stops
it once after the final loop or on failure. Media still starts separately per
loop. Both drain barriers, card retirement, comment timing, source admission,
epoch checks and the original attachment deadline remain unchanged. A snapshot
signal records loop-end waits without ending the worker. Wait totals, wait lines,
cut-outs and handler failures are reported per loop; worker progress counters are
explicitly cumulative for the session.

For an unpaid run, the existing label stand-in loads exactly the requested gold
streams once. The standalone single-stream default remains available. No gold
rows are edited, no missing frame terminal is invented, and no old event handler
is replayed to reconstruct evidence. Production worker, application, order,
contract and scoring files are unchanged.

## Focused regression evidence

The new lifecycle test file contains six cases. On the preceding source commit
`38adaf47d608f2caffdcf95e3191fa15292829c3`, the same tests produce six failures.
The two-loop case reproduces a missing real prefix and no ready attachment in its
second loop. The repaired source passes all six cases, and the related offline,
progress, cap, posting, watch and barrier tests: **52 passed**.

```sh
python -m pytest -q tests/test_c5_worker_lifecycle.py tests/test_c5_offline.py \
  tests/test_c5_offline_frame_evidence.py tests/test_c5_progress_stats.py \
  tests/test_c5_live_cap.py tests/test_c5_live_seen.py tests/test_c5_live_watch.py \
  tests/test_c5_barrier_two_checks.py
```

The two-loop test uses the in-memory application and actual worker, card handlers,
producer admission and frame-evidence evaluator. Process transport and timing are
controlled. It verifies two real card/order chains, preserved prior terminals,
rejection when an old terminal is omitted, no repeated effects, and a distinct
new card after the surviving worker receives retirement. Separate failures during
second-media setup and snapshot collection verify cleanup. The gold test checks
all 500 existing rows through the label stand-in. The snapshot test requires a
new process report instead of accepting a previous snapshot.

No further five-loop or paid run is included. These focused checks do not establish
the number of real orders recovered, model quality or latency, receipt images,
human workload savings, live-platform behavior, or acceptance of the product.
Earlier failed results remain unchanged. Full-suite output is recorded separately
at the exact submitted head.
