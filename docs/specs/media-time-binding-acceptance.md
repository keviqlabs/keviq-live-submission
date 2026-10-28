# Media-time binding acceptance

Test-only draft, based on DEC-101 rule 1 in `current-item-and-stock-rules.md`. Dependency: the app/PostgreSQL
test seam and shared database lock from #47. Production baseline: main `650fe39` (after #47 merged).
No worker, measure, app, schema, price authority or order-state implementation is changed.

The tests use real handlers, app routes, PostgreSQL `keviq_test`, the simulated messenger and moderator.
Only frame recognition and label classification are scripted. No model transport is available.
The two earlier expected-failure probes in #47 are consolidated into these complete timelines; these tests
are ordinary failing tests, not xfails. Merge only with an independently reviewed implementation that makes them green.

## Clocks

Media seconds are epoch-relative synthetic timestamps. A monotonic test clock advances ten logical seconds per
wall second. Every module's `now_ms` is patched in the test process only; store timestamps and source timestamps
therefore share that clock. The card brain's sleeps are divided by ten while HOLD_MS remains 4168 logical ms.
Label batch waiting is 1/10 wall second, and vision timeout is 10/10 wall seconds. ATTACH_WAIT_MS is configured
to 4000 logical ms in the labeler test seam; main has no such wait and ignores the test attribute.
No inference latency or live-platform performance is measured. Controlled frame completions are fixture inputs,
not evidence from gold injected into a runtime. The shared file lock is held for the whole test command.

## Required timelines and assertions

1. **Switch before recognition.** A (sneaker, 150000) is visible at 10. B (boot) frame 12 is received at 12,
   completes at 16, and creates a draft without price. Frame 13 is already received. The complete-variant order
   comment at 12.5 must attach to B, wait for B's approved price at 18 (200000), then pin B's new priced version
   before confirmation. No order before that approval, and no order line on A at any time.
   Main resolves the comment immediately to A and sends a confirmation at A's price.
2. **Price before the new card.** P (shirt, 120000) is visible at 5. The line "giày này 150 nghìn" ends at 9.5,
   arrives at 10; A's sneaker frame at 10 completes at 13.8. The real 4168-ms hold must allow the line to price
   A after its draft exists. The order at 11 waits for that later approved A version, at 150000. No line on P,
   no 120000 order and no priced card version whose write precedes its source or frame evidence.
   Main can price A within this hold, but its earlier label remains bound to P and never drafts the expected A order.
3. **Late result and reappearance.** A at 10 (150000), B at 20 (200000), A again at frame 30, recognition written
   at 32.5. A late distinct result for B's old frame 20 arrives at 33; B is seller-edited to 210000 at 33.5.
   The stale result must add no card version, and `/cards/live` plus an order at 34 must still select A at 150000.
   Main's stale-result guard already passes the first clause. The shared ordering still ranks B by its original
   card creation, so the endpoint/order clauses fail; the test does not claim the old stale guard is broken.
4. **Expiry / incomplete evidence.** A at frame 15 (150000). A frame-19 read remains pending until 25. B's frame
   20 recognition is delivered out of order at 23 (200000). A complete-variant comment at 20.4 cannot be bound
   while frame 19 is pending. At 24.4 its 4000-ms deadline expires: NEEDS_SELLER with exactly [B,A], no customer
   item-selection question and no confirmation. No earlier order may silently borrow A's price.
   Main creates/confirms an A order immediately instead of waiting for the watermark and routing expiry to the seller.

The two-candidate line/decision representation requires the separate contracts-owner approval described in
DEC-101. Tests assert the proposed optional `candidates` on the order line but do not add it to a schema.
Changing an event field requires the existing contracts process. No stock-hold or stock-reply feature is requested here.
