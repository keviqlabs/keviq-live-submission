# Slow vision integration regression

## Scope and acceptance

Exercise the real HTTP app, PostgreSQL test store, worker runner, comment shield and simulated moderator.
Inject a `Handler(event, ctx) -> list[dict]` into `HANDLERS["frame"]`; never invoke a model or change worker code.
The worker dependency is pull request #45, initially `0f08af47f4a3d0da1a3c2a9255a52f515c94dfb7`.
The regression baseline is `bf620a4`. Use a separate test-only pull request and an independent reviewer.

- Replay all 351 frames at 1 logical fps and all 100 synthetic loop-1 comments across 351.5 logical seconds.
  Include `c5-1-095` at 335825 ms and `c5-1-098` at 339875 ms; do not stop at 330 seconds.
- Alternate fake vision service times of 2 and 3 logical seconds. Keep frame and service clocks at the same scale.
- First stored `comment_labeled` with `action=hide` minus the comment's stored `ingested_ms` is at most
  5000 logical ms for every digit-phone comment. Also verify the simulated moderator's actual hide record.
  `latency_ms` is not the assertion's clock. Word-phone model detection is outside this shield regression.
- Vision waiting and in-flight counts are each at most one. At every sample,
  received = done + skipped + stale + failed + waiting + in_flight.
  Every frame ID has one terminal outcome, or remains explicitly pending; duplicate terminal IDs fail.
- Use the actual measurement progress reporter and loop-end barrier. It cannot pass with pending work;
  a stopped worker with outstanding work gives INCOMPLETE. A fully drained run gives SETTLED.
- Switch-before-result and price-before-card retain strict expected failures until media-time binding is repaired.
  These are controlled pending-result reproductions, separate from the timed replay.

## Clocks and limits

Default speed is 20:1 (351.5 logical seconds / 20 = 17.575 seconds of scheduled wall time).
`SLOW_VISION_SPEED=1` runs the same scheduled replay without compression (about six minutes including drain).

Scaled together: input arrival waits, fake 2/3-second vision sleeps, the runner's 10-second vision timeout,
the label batch's 1-second timer, and progress reporter intervals. Real DB ingestion time is measured without
patching the clock and multiplied by speed only for reporting and the 5-second bound.

Not scaled: source/media timestamps (preserve the complete 351500-ms timeline), `now_ms()`, PostgreSQL ingestion,
HTTP/DB execution, the barrier's polling and 2-second freshness check, or its wall-clock test deadline.
`window.HOLD_MS=4168` is untouched and is not exercised by the timed frame/shield replay. The controlled
price-binding expected failure sets HOLD_MS to zero to isolate candidate selection, not to measure its latency.
The 5-minute image rule is untouched and is not tested: fake product detections do not produce SAM images.
In particular, a compressed run is not evidence that this wall-clock rule passed; only the scheduling regression
and shield bound are measured. No real model, image quality, live Facebook, rendering, or C5 quality gate is claimed.

The ASGI transport buffers streaming bodies. The test client requests the app's real SSE endpoint with
`follow=false`, then the unmodified runner opens the next cursor read. This tests app/store/runner integration,
not network chunking, reconnects or TCP backpressure. Tasks created under the ASGI app context are tagged as
server infrastructure and excluded from worker background counts; worker lane/label tasks remain counted. All writes remain in a unique session in `keviq_test`.
The test holds both a local test-database file lock and a PostgreSQL advisory lock; it never deletes run rows.

## Verification and delivery

Run the compressed tests in `make check`, show the two baseline failures (head-of-line delay and missing stats),
then run the same replay at 1x. Save red/green/full logs, structured run summaries and SHA-256 in the evidence NOTE.
Record exact worker and test revisions. No paid probe is authorized by this task.
