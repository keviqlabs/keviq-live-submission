# Proposed pre-order attachment contract (v1)

**D-029 is a supplemental product proposal pending product-owner approval.** This change defines schemas and
semantics for review; it implements no producer, route, projection, authentication or order flow. It does not
authorize runtime work or deployment. Required order fields, order states and price authority remain unchanged.

## Product choices requiring approval

1. A frozen snapshot with **zero cards cannot be selected**. The seller can keep it unresolved or reject it;
   late recognition does not add a candidate. Creating a replacement snapshot to rescue that comment is outside
   this proposal.
2. **Reject before an order exists only closes the pending intent in the console.** It sends no customer
   message and creates no placeholder or CANCELLED order. This is a product choice, not an existing behavior
   proved by the schema tests.

The other proposed choices are a frozen snapshot of 0–2 real cards, a seller's audited keep/select/reject
decision, and waiting for the selected card's own SKU and approved price. None is treated as runtime approval.

## Event and immutable snapshot

`comment_attachment` is a new event type with envelope `v: 1`. Its logical versions are separate from envelope
version v: append `version: 1`, then 2, 3, and so on; never update or delete a prior event.

| Field | Meaning |
|---|---|
| attachment_id, version | stable session/label identity and its append-only logical version |
| worker_epoch | server-bound owner epoch for ready; may be null for an app-owned expiry without a worker claim; frozen thereafter |
| readiness_evidence | required only for outcome ready; frozen terminal frame attestations, after-frame ref and worker observed_cursor |
| comment_id, comment_event_id, label_event_id | the real source comment and its eligible final order label |
| comment_ts_ms | original comment media time used to find current/previous |
| comment_ingested_ms, deadline_ms | original stored receive time and original deadline: receive time + that comment's configured ATTACH_WAIT_MS |
| snapshot_at_ms, snapshot_cursor | time the first snapshot was made and highest history cursor read fully to construct it |
| candidates | 0–2 actual current/previous cards, in that order, omitting absent and duplicate cards |
| outcome | immutable ready or expired outcome of the initial attachment wait |
| action | ready / expire for version 1; select / keep_unresolved / reject for a later seller decision |
| selected_card_id | selected card ID or null; expiry alone does not select even a sole candidate |
| actor, reason | server-assigned system/seller authority and the recorded decision reason |

Each candidate is exactly `{card_id, card_version, card_event_id, appearance_event_id,
appearance_frame_ts_ms}`. It contains no price, SKU, stock, order line or amount. The version event is the real
version known at the snapshot; the appearance event proves that card's relevant appearance at comment time.
They need not be the same event: a later price edit does not create an appearance.

Runtime must establish that comment, label, cards and appearance events all belong to the envelope's session;
the label names this comment and is eligible for this order flow; references match the recorded card/version;
candidate appearance is at or before comment_ts_ms; and each referenced event was included by snapshot_cursor.
References need not all have the same source value as the attachment or comment. Check the accepted source
binding and causal chain for each event kind: a bound replay frame/product_seen/card chain and sim comment/label
chain can be valid in the same session. A source tag alone grants no authority; neither matching nor differing
tags replace the required session, epoch, cursor, provenance and source-transport checks.
Distinct card_id values are required, even when their referenced versions differ. JSON Schema's uniqueItems
only detects identical candidate objects; it cannot prove logical uniqueness or reference history.

Version 1 has actor system. `action: ready` requires the specified frame-readiness evidence and at least one
real card, outcome ready and the current candidate selected. No current card means the wait cannot become
ready; it expires instead. `action: expire` has outcome expired and null selection, with 0, 1 or 2 candidates.
An unhandled lower frame prevents readiness even if a newer recognition has completed.

The initial snapshot, outcome, comment/source refs, worker epoch/evidence, session, original clocks and deadline are copied unchanged
into every later version. Late recognition cannot add or replace candidates, change expired into ready,
move the deadline or choose an old card's price. Only decision action/reason, decision time, authority,
selection when permitted, version, envelope event ID/timing and causal references advance.

ts_ms is the actual time of the decision represented by that version. It is never set back to comment time
or retroactively to a deadline missed during an interruption. snapshot_at_ms is also not backdated. Numeric
clock relationships, deadline arithmetic and preserving fields across versions require runtime checks.

Version 1 caused_by includes the comment and label event IDs. A seller version cites its preceding attachment
event and the selected candidate's card event, or the source label for keep/reject without a selection.
Orders later cite the attachment decision and the exact card version that supplied identity and price.

## No invented order while identity or price is missing

For the new producer, an order requires the chosen card to have a non-null SKU and an approved price on its
latest live/edited version. Price alone is insufficient. The order pins that real version; proposals,
catalog prices, customer text and another card's price do not supply it. These are producer/route semantics;
historical order v1 shapes, including nullable line SKU, remain valid and unchanged.

| Initial attachment situation | Proposed continuation |
|---|---|
| ready, current card lacks SKU or approved price | selected identity is fixed; no order yet; wait for that same card's qualifying version |
| expired, exactly two candidates whose frozen referenced versions both have SKU and approved live/edited price | preserve the existing two-candidate D-027 path: snapshot first, then DRAFT immediately to NEEDS_SELLER, using the first candidate's real version/price |
| expired, 0/1 candidates, or either frozen candidate lacks SKU or approved price | show the actual snapshot before an order; no order, amount, stock hold, confirmation or receipt |
| seller selects a candidate lacking SKU or approved price | record selection, hold that card identity, wait for its own qualifying version; do not resolve again when other cards arrive |
| selected card remains retired/unidentified/unpriced | keep pending for seller rejection; do not fall back to a prior or different card |

The routing branch is derived from the initial snapshot's referenced versions. Later pricing or recognition
does not automatically convert an unselected pre-order expiry into the D-027 order-choice path. A new card
version can resume a selected/ready intent only when it belongs to that exact selected card and satisfies
the SKU and approved-price requirements. Existing variant, quantity, stock, price authority, threshold,
messaging-window and customer-confirmation rules then apply. An already pinned order is never repriced.

## Seller decision before an order exists

Proposed route: `POST /api/sessions/{id}/attachments/{attachment_id}/decision`.

Body is `{attachment_version: int, decision: "select" | "keep_unresolved" | "reject", card_id?: str}`.
card_id is required only for select and forbidden otherwise. The body cannot supply actor, SKU, price,
candidates, outcome, clocks or deadline. `requests/attachment-decision.schema.json` proves only these shapes.

The existing seller console boundary supplies authority; the server assigns actor seller. Customer text or
automated classification cannot grant it. This adds no assistant permission or new seller-identity system.

- Unknown session/attachment: 404. Invalid shape or select outside the frozen same-session snapshot: 422.
- A new decision requires the latest expected attachment_version, no prior rejection and no order for this
  comment; state/version conflicts or a concurrent writer produce 409 with no side effect.
- select is allowed only on an expired, unselected intent and names one existing snapshot candidate. It can
  choose an unidentified/unpriced card. Once selected, identity cannot be changed through a later select,
  keep_unresolved or an old request; keep/reject remain available before an order exists.
- keep_unresolved appends an audited seller version, preserving null selection when no card was selected or
  the existing selected_card_id while waiting for that card's SKU/price. It changes no snapshot/deadline,
  makes no new selection and sends no message or order. This differs from `sku: null`
  at `/cards/{card_id}/item` (D-028), which is a card no-op with no new event or version.
- reject appends a terminal seller decision on the intent, preserving any already-selected identity and
  immutable snapshot. It closes only the console pending intent, with no customer message or fake order.

All checks, event appends and any resulting order handoff must be serialized against order creation for the
same comment. Once an order exists, only its existing approve/reject route is authoritative. There is no
keep action on order-decision, and a new pre-order decision must be refused. An exact previously accepted
retry may return its original canonical result even after order creation, without making a new choice.

## Compare-and-swap, retries and recovery

The app remains the only event-store writer. The storage invariant is uniqueness of
`(session_id, attachment_id, version)`, not just event_id. A different event ID cannot bypass this key.
Version n+1 must cite canonical version n and match its frozen fields. A refused request writes no attachment,
order or message. Neither schema validity nor actor text proves write authority.

Attachment identity is deterministic for the session and label; event ID is deterministic for that identity
and logical version, within the envelope's identifier limits. Runtime must enforce those derivations and the
logical uniqueness key atomically; the chosen ID encoding cannot substitute for a history/CAS check.

An accepted request is identified by its expected attachment_version and intent (decision and card_id, when
present). A retry of that same intent returns the event already stored for version expected+1. Compare
request intent, not a newly generated timestamp, reason or event ID. Different intent against that expected
version is 409. After an append reports a duplicate, read the canonical event and verify intent and frozen
fields before returning success. One accepted seller action counts once; repeated delivery adds no event,
intervention count, order or message.

Rebuild from comment, final label, attachment versions, referenced card history and orders/caused_by. A rejected
intent is closed; an intent with an order follows that order's history; a ready/selected intent without an
order waits only on its selected card. An expired unselected intent waits for seller choice. Keep decisions
remain visible as audit history and never reset the deadline. A crash between selection and DRAFT must resume
from that selection; one between DRAFT and confirmation resumes the same order instead of drafting/sending twice.
Use the existing comment-derived order idempotency in addition to attachment CAS, including duplicate labels.

If no attachment was stored before a restart, retain the original comment.ingested_ms and original configured
bound for that comment. Loss of in-memory frame progress cannot prove ready. Before its original deadline,
wait for provable evidence; after it, record expired at the actual recovery time. A late label likewise has no
fresh wait budget. The required label reference is not removed to pretend a decision existed before that label.
Until the final label exists there cannot be a valid attachment referring to it; a late label causes an actual
late expired outcome, counted as a miss in the original timing denominator. For such recovery, include only candidates demonstrably known by the original deadline;
exclude late recognition even if it is present when history is read. Record recovery-late in reason and report
the actual delay. The actual history cursor and snapshot time stay truthful; a recovery-late run cannot claim
the timing acceptance passed. A previously stored snapshot always wins over a newly computed one.

## Proposed worker-only system ingress

The readiness owner is the single worker running vision and label attachment for that session and ownership
epoch. It maintains received frames, pending reads and completion through the card post. A higher completed
frame does not erase an unfinished lower frame. The app remains the sole store writer; the separate HTTP
worker reports evidence rather than submitting a completed comment_attachment event.

Proposed endpoint: `POST /api/sessions/{id}/attachments/system` with
`{worker_epoch, comment_event_id, label_event_id, observed_cursor, readiness_evidence?}`.
`requests/attachment-system.schema.json` allows no caller outcome, ready boolean, actor, candidates, price,
deadline, logical version or event ID. Server constructs version 1 from actual source history and its clock.
It derives comment_id, attachment_id, comment timestamps, the original deadline, candidate snapshot and actor.
The request observed_cursor is the worker's history boundary; snapshot_cursor is the server's fully read
history boundary used for the actual snapshot and is never invented from the caller's number.

### Authentication, ownership and rollout

The proposed ingress uses a worker-only bearer credential in the Authorization header. The server verifies
the credential and looks up its **durable server-controlled binding** to the exact session, active worker
epoch and system-outcome scope. A body epoch or source string cannot create that binding. Deployment must
provision/revoke this binding and preserve its history; this contract creates no credential, secret, account,
configuration or provisioning endpoint. There is no new public route to issue epochs or grant authority.

The durable binding records the authorized worker principal, exact session, epoch, system-only scope and
activation/revocation history, together with the original session wait bound and source-ordering capability.
Only the deployment authority can provision/rotate it; no caller request can register or advance an epoch.
The active binding is checked on every request and its history remains available for existing outcomes.
Only one worker ownership epoch is active per session. A restart/takeover fences old epoch requests and
old readiness attestations; it cannot inherit a complete watermark merely because memory was lost. Recheck
the credential's epoch capability and active server binding at the CAS commit point so a revoked writer
cannot authenticate under the old binding and write after takeover. The old credential must lose that
capability; accepting any caller-provided new epoch under an unchanged universal credential is insufficient.
Unaccepted old terminal claims cannot be relabeled with a new epoch. Previously accepted outcomes keep their
historical provenance and selection; this bounded full-evidence proposal does not claim cross-epoch prefix reuse.
Retain the original session wait bound and original comment ingest times independently of process memory.
Server recovery uses those durable sources, configured ownership history and any existing attachment event;
it does not depend on a timer or binding that was never recorded. A missing valid owner configuration cannot
be filled from an event body or silently create a ready outcome.

The credential grants only access to evaluate initial system outcomes for its bound session. It grants no
seller select/keep/reject, SKU, price, generic event-write or cross-session authority. The seller route requires
its separate console authority; treating a worker token as seller approval is forbidden. The server stamps
actor system for an initial outcome and seller for an authorized console decision.

Missing/invalid credentials: 401; wrong scope/session/epoch or revoked ownership: 403; unknown referenced
session/source: 404; bad shape/source relation/future cursor: 422; canonical identity/history conflict: 409.
Unconfigured ingress fails closed (503). Do not enable this producer/new-flow consumer path until binding,
authority guards and readers are configured and tested. There is no fallback that accepts a caller-supplied
attachment through generic ingest or drafts directly from a new-flow label to bypass missing ingress.

Generic `POST /api/sessions/{id}/events` must reject caller-supplied comment_attachment events, including valid
system/seller actor text and worker credentials; it is not this ingress. Alternate event IDs cannot bypass
logical-version uniqueness. No runtime guard, endpoint or authentication is implemented by these schemas.

### Evidence and what the server can establish

readiness_evidence contains an after_frame_event_id and handled_frames. Each terminal record names
frame_event_id and result: no_product_seen (read finished with no emitted product_seen), read_error (terminal
read failure under the approved handled rule), or product_seen. A product_seen result additionally requires
the real product_seen_event_id and all card_event_ids whose posts complete that result. An unread frame
discarded by queue replacement is **not** a no-product read or read error; this proposal does not count that
skip as handled. If readiness cannot be established under the approved rule, it remains pending.

| Work result | Attachment handled? | What is still required |
|---|---|---|
| product_seen read complete | only after all required card posts are stored | verify frame/product/card causal refs and actual stored versions |
| read complete with no emitted product_seen | yes under the existing rule | authenticated terminal attestation; not positive proof of identity |
| genuine terminal read error | only the error skip allowed by the existing spec | authenticated error completion; not an unfinished task renamed error |
| queue replacement without a read | no, even if runner calls the task settled/skipped | remains a coverage hole; cannot fabricate a no-product receipt |
| stale/duplicate runner dispatch | no automatic handled claim | a canonical terminal receipt for that actual frame is required |
| after-frame received | closes the input set only with the validated source fence | its read need not finish for this comment |

Before accepting ready, the server reads source history itself and checks:

- active authenticated owner/session/epoch; original comment and final order label match; observed_cursor
  is real and every reported source ref is at or before it;
- the after-frame belongs to the session and has frame time greater than comment_ts_ms;
- terminal records have distinct frame IDs and cover **every received frame at or before comment_ts_ms**
  known to the server at the decision boundary, with no hole below a higher completed frame;
- reported product_seen belongs to its frame, and every required card event has actually been stored with
  matching frame/provenance/causality; a pending/refused card post is not completion;
- candidate and appearance refs are derived from the server's history, with the frozen snapshot checks above.

An after-frame ref alone is **not** a complete source fence. Ready requires a single authoritative media source
bound to the session/epoch with an explicit ordered-prefix guarantee: once its frame after t is accepted, it
will not deliver another frame at or before t. The server checks observed receipt order and source provenance
through that fence and must enforce the closed prefix at source intake. A later lower-frame submission is a
source-order violation, not permission to rewrite an existing snapshot. This constraint must be established
by the runtime's source transport/guards; it is not inferred from the source string, a cursor, one future-frame
ID or historical generic frame ingest. No frame schema or extra source route is added by this proposal. If
source ownership, prefix completeness or enforceable ordering is unavailable, there is no ready proof: stay
pending until the original deadline, then expire. Sparse terminal records or a verified current card cannot
substitute for that source fence or full required-frame coverage.

Terminal no-product/error and the absence of a pending read are **attestations by the authenticated readiness
owner**, backed by its state machine and source references. The server can verify coverage, refs and stored
card posts; it cannot independently observe worker memory or prove that a physical read occurred. Authentication
alone proves sender authority, not readiness. A valid boolean, actor, event ID, credential, sparse high watermark
or schema-shaped record is insufficient. Runtime tests must establish that the owner emits terminal evidence
only after the actual approved completion point and cannot omit a blocked lower frame.

For outcome ready, the event persists worker_epoch and readiness_evidence, adding the request's observed_cursor
to that frozen evidence. Later versions copy it unchanged. For outcome expired, no readiness_evidence is stored:
expiry is absence of timely readiness, not a claim of completed reads. An app-owned expiry without a worker
claim sets worker_epoch null rather than inventing an active worker. A genuine accepted worker context may
be retained on an expired outcome; later seller versions preserve that epoch or null exactly. The ingress
request still requires a non-null authenticated active epoch; null is not a worker bypass or seller authority.

### Published evidence bounds for the approved five-lap workload

This draft uses bounded full evidence, not a new durable-prefix storage/progress protocol. The approved
frozen workload has 351 frames per lap and five laps in one run, so each request/event readiness list allows
at most **1755 terminal records**. At most **two required card-post refs per frame** cover the current card
and a previous draft's retirement in the present handler. If a third post is actually required for handled,
do not omit it to fit: full output cannot be proved within this bound, so readiness fails closed.
These are published proposed protocol limits;
they do not shorten the approved run to one lap or alter the source workload or acceptance denominators.

The ingress raw JSON body and serialized ready event are limited to **1 MiB (1048576 bytes)**. A synthetic
five-lap request with 1755 distinct product-result records, every identifier at its maximum 96 ASCII
characters, two card refs per frame and a 64-bit cursor serializes to 846461 bytes in compact JSON, leaving
202115 bytes. A full ready-event sample is 848828 bytes, leaving 199748 bytes: all event fields including
four timing fields, two candidates, two causal refs, a 64-character ASCII session ID and a 256-character ASCII
reason are present. Sample clocks/cursors use nonnegative signed-64-bit values, card versions use positive
signed-64-bit values, and logical version is 1. Both samples use Python json.dumps with ensure_ascii=True,
compact comma/colon separators and UTF-8, without compression. These are full-shape measurements in that
declared domain, not an absolute maximum of every schema-valid body: reason length, causal-ref count and
numeric values do not all have schema maxima. The raw byte guard remains authoritative for other bodies.
The schema tests validate a full-size request and event; their size bound is not a runtime latency benchmark.
The worker uses compact encoding; raw byte enforcement is an HTTP/app guard, not JSON Schema. Whitespace
or escaping does not bypass the byte cap merely because the parsed value would fit a shape.

Over byte cap is 413; over record/ref shape limits is 422. Refusal accepts no truncated readiness fact and
writes no ready outcome, order or message. The app's independent timer still expires the valid intent at its
original deadline. Never slice records, skip a hole, reset the epoch/session or divide the approved workload
to evade the bound. A ready event has the same record/ref and byte bounds as its ingress evidence.

Runtime must measure validation/storage/check time on the real approved five-lap workload and report misses,
memory/byte costs and resulting recall/seller effort. This draft proves representability only; it does not
promise 4000-ms execution or support beyond this published full-evidence bound. A longer workload or verified
prefix/delta optimization requires a separate contract/product decision, not an undisclosed truncation.

### Deadline, timer and canonical initial outcome

Before the original deadline, only fully validated readiness plus a real current card can produce ready.
Otherwise return 202 pending without an attachment/order/message. Missing proof is not permission to expire
early. The app's independent deadline task must use the original comment.ingested_ms plus the retained bound,
not the request time or classifier completion. The app's new-flow deadline projector follows eligible final
labels for the sessions in its durable binding/configuration; it does not require the worker to submit another
request at expiry. A pending response does not create an unrecorded source of timing authority. Reconstruct
pending deadlines from those stored comments/labels and binding/configuration after an app restart; a worker
that hangs after an initial pending request cannot prevent app expiry. An ingress retry allocates no new budget.

If a label or initial request has not arrived before deadline, the app cannot claim the missing input exists.
A known final label and registered new-flow session let the app expire without a request or active worker,
recording a null worker epoch when there is no worker claim; a missing required label delays the first valid
event until it is actually present. Missing configuration for source/wait timing fails closed rather than
fabricating a session binding or clock; mere absence of an active worker does not block app-owned expiry.
Those interrupted/late cases retain the original deadline and are timing
misses when emitted late, including the original denominator; there is no assertion of a timely event that
could not have been written.

At or after the original deadline, an unresolved intent records expired with actual server time and the
eligible frozen history, even if a late request now carries a complete proof. A stored canonical ready outcome
already written before deadline is not replaced. If the label/request or recovery occurs too late to emit on
time, write only when the required source inputs exist, mark the lateness and count the timing miss; never
backdate, remove required refs or omit that comment from the timing denominator.

The first system outcome competes on the same deterministic attachment identity and logical version 1.
Once stored, return/read that canonical outcome on repeated evaluation; do not replace its snapshot with a
later one or turn expiry into ready. A same-source evaluation retry can therefore receive the existing expired
outcome rather than its newly hoped-for ready outcome. Conflicting comment/label identity is 409. App timer,
worker ingress, seller decision and order handoff must share CAS/serialization and recovery invariants.

Runtime verification must cover authentication/scope/epoch fencing, generic-ingest forgery, incomplete or
cross-session proofs, queue replacement, refused card posts, a blocked lower frame, concurrent timer/readiness,
canonical retry, worker/app restart and late labels/requests. Schema tests prove only shape/local consistency;
they do not prove authentication, cryptography, worker execution, readiness or timely delivery.

## Remaining identity eligibility and compatibility limits

**SPEC_GAP: D-028 currently permits SKU assignment only to an unidentified draft.** An unidentified card that is
already live/edited with a price does not qualify for that route. This proposal does not silently widen D-028.
Selecting such a card holds its identity without an order; it can be kept pending or rejected. A valid way to
supply its SKU needs a separate owner decision before that continuation can be implemented. No status change,
card clone, generic-ingest SKU assignment, catalog price, automatic SKU or rebind is a substitute.

The new event type is v1; existing event shapes, comment_labeled, order required fields/nullability and order
states are unchanged. New validators still read old data. Older strict validators reject this event type;
older consumers that ignore it may incorrectly draft from a label. Schemas/readers/authority guards must be
coordinated before any new producer or route is enabled; do not let the legacy label fallback bypass attachment
authority on comments in the new flow. Producer gating is not solved by adding this schema file.

Schema tests cover event/action/request shapes, 0–2 references and rejected price/authority fields. They do
not test routes, membership, permissions, real frames, chronology, frozen history, CAS, recovery or effects.
C1/C3/C5/C8 definitions, gold, caps and paid gates are unchanged; a wait event does not automatically count as
a C1 action or make an abstention a correct reference. Pending price and late recovery remain reported honestly.
Rescoring must report the number of intents stuck for missing SKU on a live/edited unidentified card, empty
snapshots, missing price and insufficient evidence, with their recall and seller-intervention impact. Keep all
eligible comments in the original denominators: do not drop stuck/late samples or count abstention as correct.
