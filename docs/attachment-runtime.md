# Media-time attachment runtime

A session enters the attachment flow only through a durable, server-controlled binding. The application
defaults to the existing flow for unregistered sessions. Generic event ingestion always refuses
`comment_attachment`; a source string or actor field cannot enable the new flow.

Trusted bootstrap code constructs `binding_definition(...)` and calls `app.state.attachments.configure(...)`.
This is an in-process deployment boundary, not an HTTP provisioning route. Bindings persist credential digests,
session, epoch, accepted source kinds/transports, original wait bound and revision history. Rotation needs fresh
credentials, retains the source stream and original wait, and shares the session transaction lock with writes.
This implementation creates no deployment credential or configuration.

The worker receives a session mapping through `Runner(..., attachment_auth=...)`, with `epoch`, `source_token`
and `system_token`. The system token only evaluates initial outcomes. Separate source grants should restrict
media intake, comment intake and worker output to their required event kinds and accepted transports. None
grants seller, SKU, price or order-writing authority. Seller actions retain the existing local console boundary;
worker Authorization headers are refused there. This does not add a public seller identity system.

Source intake enforces the ordered frame prefix and stores immutable transport receipts. The worker records
actual terminal reads and completion through every required card post. A bound session's vision lane reads
every frame, up to `VISION_READS_PER_SESSION` (default 4) at a time, and posts the results in media time order;
only an unbound session's lane replaces a waiting frame with a newer one. Frames still in a lane when the worker
stops are lost, since the stream cursor moves when a frame enters its lane, and a comment behind them stays
pending until its deadline. A bounded read-through can observe stored output and label references without
dispatching handlers, rerunning inference or advancing processed progress. The server checks the complete required prefix, causal
references, accepted transport and active epoch; physical read completion remains the authenticated worker's
attestation. Authentication alone is insufficient.

The initial wait ends at the original stored comment receive time plus its retained bound. Startup recovers
durable pending work before serving requests; an independent projector keeps the timers running without a
worker request. History caches are reused only after authoritative session-head and binding-revision checks
under the same transaction lock. Background polling reads revision metadata, while each write checks its full
active binding. A reader connection sees committed history while the writer transaction
retains its own writes. Attachment, order, message and input-consumption effects commit together. A request
that completes preparation after deadline cannot store a late ready decision.
The two-candidate expiry writes its outcome, complete order history, message, receipts and unique comment
reservation in one database statement within that transaction, using the existing order-rule partials.

Current/previous use frame-time appearance, including returns; price or SKU edits cannot move an old item to
the top. Reference resolution follows D-030. A selected card waits for its own non-null SKU and approved
live/edited price, then pins that version. An expiry never automatically chooses a candidate. Two eligible
priced candidates use the existing order approve/reject route with explicit membership checks. Otherwise the
pre-order route offers select, audited keep or console-only reject. Once an order exists, that route refuses
new decisions; exact accepted retries return their canonical result.

The full evidence limits remain 1755 required terminal records, two required card refs per result and 1 MiB
for the raw request and serialized ready event. An after-frame is still required; future frames do not reduce
the permitted required prefix. Over-limit or incomplete evidence is never truncated into ready. Late labels,
recovery and missing prerequisites keep the original deadline and remain misses or pending outcomes.

An unidentified live/edited card still cannot receive a SKU through the draft-only item route. Empty snapshots,
missing prices, missing identities, unavailable variants and incomplete evidence remain visible limitations;
they do not justify another card, a placeholder price or a changed denominator. The legacy effort counter
does not cover every new seller action or refused/no-op attempt. A complete intent/effort adapter and the
frozen-workload rescore remain separate verification work; these runtime checks do not establish the final
intervention-rate or live acceptance gates.
