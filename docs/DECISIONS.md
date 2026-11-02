# Technical decisions during the hackathon

Newest on top. Contract changes must have an entry here.

## 2026-11-02 — C9 delivery and C11 current state

**D-037 C9 delivery definition and C11 current-state wording.**
Decided by the Founder on 2 November 2026 (07:03 ICT). C9: the judges' entry point is the public repository run from
the README; a hosted URL is optional. C11: the sentence that implied an RTMP input is replaced by the current state (the
media worker reads recorded files; live RTMP input is not implemented). The README line on the hosted demo now says to
include a link and access instructions only if one is deployed. No metric, threshold, event or schema changes; `v`
unchanged.

## 2026-11-01 — Wonder exception for the model comparison measurement

**D-036 (provisional number) Muse Wonder may be called only inside the product-identification model comparison, which is a measurement, with a 5.00 USD cap for the whole experiment that sits outside the D-006 meter. Nothing reaches production.**
The number is provisional: D-031 is held by #77 and D-032 by PR #82. Whoever merges later renumbers.

Founder authorization, 2026-11-01, verbatim:

- Question: "Luật 5 cấm dùng Muse Wonder tuần này. Anh có cho phép ngoại lệ dùng Wonder chỉ trong thử nghiệm so sánh model này, trần tổng 5 USD, không đưa vào sản phẩm không? Câu trả lời của anh sẽ được trích nguyên văn vào hồ sơ."
- Founder: "Cho phép, trần 5 USD"

Scope and limits:

- Only `measure/model_compare.py` calls Wonder. `meta/pricing.py` still lists it in `BLOCKED`. No production schema, prompt, model setting or price changed.
- Inside that module, Wonder is priced at 4.00 USD input and 20.00 USD output per million tokens. This price is an assumption with no cited source in the repo (`meta/pricing.py` lists no Wonder price). No cached input price is known, so cached input is charged as full input.
- The 5.00 USD cap covers all three configurations together (Spark minimal, Spark high, Wonder). The experiment's own meter enforced it before each call. The runner wires that meter (`CapMeter`) into the client in place of the project `Meter` (`measure/model_compare.py:268-271`), so `Meter.check` never ran: the D-006 caps of 30 USD/day and 300 USD total were not checked for these 560 calls. Their `model_call` events also went to a JSONL file, not to PostgreSQL. 3.7242853 USD must be added to the 2026-11-01 meter by hand.

Spend from the `model_call` ledger, not reconciled with Billing: 3.7242853 USD over 560 calls. By configuration: spark-minimal 0.27568665, spark-high 0.98927865, wonder-high 2.45932. The 5.00 USD is the cap, not an amount spent. Add 3.7242853 USD to the 2026-11-01 model spend by hand.

Evidence: `docs/evidence/2026-11-01/model-compare-NOTE.md`. No contract change; `v` is unchanged.

## 2026-11-01 — optional catalog kind evidence

**D-035 Prepared candidate: use explicit catalog kinds only to refine a new `other` card.**
This is a narrow extension of the product-identity work, authorized for local implementation through
project coordination. It does not claim release approval; independent review, final integrated checks
and the Founder's Ready/merge decision remain pending. D-026 remains as the historical decision; this
entry supersedes only its restriction on optional vision kind vocabulary and string attributes below.

When the active seller-approved catalog contains explicit `kind` values for `other` items, the existing
image call receives their kinds and colors, without SKU, price, stock or expected answers. The coarse
category and minimal reasoning effort stay unchanged. `product_seen.attributes.kind` and
`attributes.identity_state` carry optional string/null evidence already allowed by v1. No schema,
API, event type, acceptance metric, threshold, order permission or confirmation rule changes.

Code assigns SKU only while creating a new card and only when a clear observation above the existing
confidence threshold matches exactly one catalog item by kind and observed color. For new typed observations,
missing/unknown kind, missing/mismatched color, unclear/low-confidence evidence and multiple matches stay
unresolved. Legacy events with both optional attributes absent retain coarse unique matching; the presence
of either attribute selects strict typed matching, including explicitly null or unclear values. There
is no first-item, price, previous-SKU or previous-price fallback. A later clear observation creates a
fresh unpriced card rather than changing an unresolved card's SKU. Repeats and item switches include
kind/state in continuity checks; unresolved cards still occupy the normal current/previous position.

The existing media-time price selection is retained. For refined identities, a price additionally requires
exactly the same explicit kind in the speech text; unresolved, unnamed or conflicting kinds wait for seller
input. Generic price-only speech can therefore lose automatic coverage. Legacy cards retain their old
speech path. This guard changes no speech prompt, schema, price source or seller authority.

Speech prompt, transcriber keywords, coarse categories, catalog data and all non-`other` behavior stay
as before. With no explicit `other` kinds, the vision prompt/schema stay unchanged. No model price or
SKU is accepted. The new combined prompt has not been measured: the saved-output replay in
`docs/evidence/2026-11-01/catalog-kind-component-NOTE.md` is component evidence only, not a C5 run,
latency result or proof of extra orders. No paid authority is granted here.

## 2026-11-01 — C5 receipt wording and manual comparison

**D-034 C5 refers to the card version quoted to and confirmed by the customer; the separate manual comparison is not a staffing estimate.**
The project owner approved both wording replacements on 1 November 2026. Independent review by the contracts
owner is still required before merge; this entry does not record that review as complete.

- Receipt wording: replace "the card version current at confirmation" with "the card version quoted to and
  confirmed by the customer", consistent with the quote/receipt version behavior recorded in D-032. This does
  not assert that every measured receipt already meets C5.
- Comparison wording: remove the unsupported staffing claim. The [single-run manual comparison](evidence/2026-11-01/manual-founder-1-NOTE.md)
  may be cited only together with n=1 and its published protocol deviations, including prior exposure to aggregate
  results and three practice runs. It is not an estimate of staff required or a measurement of staffing savings.
- Preserve all C5 targets: 500 comments in 30 minutes, order-label recall >=0.9, seller interventions <=10 per hour,
  phone visibility <=5 seconds, and the 100% receipt target. Correct order labels are not completed orders;
  intent, right item, order, confirmation and receipt outcomes remain distinct.
- Only the C5 row and this decision record change. No metric/scoring logic, schema, gold, catalog, order code,
  price authority, confirmation rule or permission changes. The proposed documentation clarification retains v1;
  the independent contracts review must confirm compatibility and the evidence references before merge.

## 2026-10-31 — video source

**D-033 The video's main source is a recorded replay with synthetic comments, not a real level-2 live session (C9 and the C4 check amended).**
C9 named one real level-2 live session as the main source of the video, with before/after numbers from that session.
Three facts in the code make such a session a poor main source. The media worker reads finished files only: there is
no live camera or stream input, and frames are stamped from the start of playback, while real Page comments carry
Facebook's clock, so a comment like "the pair just now" could be matched against the wrong card. Real Messenger sends,
real comment hiding and real confirmed orders stay closed, so a live session would show real comments beside a
simulated order path. And a live session has no labelled gold, so it cannot produce the accuracy and seller-relief
numbers (C3, C5, label F1); latency and cost alone would not carry the story, while the replay's synthetic comments
are labelled and scored with the real models.

The video is therefore recorded from the replay: a recorded product video through the media worker on the session
clock, with synthetic comments, both stated on screen, and the numbers in the end-to-end story taken from one measured
run named in the README. An optional short scene shows real comments from testers on the chosen Page entering the
console read-only (`make live-ingest`); it is labelled as such and carries no numbers. C4 is now checked by hand on
the replay, because the cards in the video come from the recording. The judges' path (replay and simulators, no Meta
account) and the stable URL in C9 are unchanged. The citation for the C5 staffing baseline remains unresolved; this
decision does not validate that baseline or establish it as "before" comparison evidence. No
event, schema or version change; acceptance stays v1. No order rule, price authority, customer-confirmation rule or
level-2 permission changes. The "RTMP input" named in C11 is not built either; that sentence is left for a separate
contracts change. Product-owner review/merge is required.

## 2026-11-01 — which card version an order keeps

**D-032 An order is locked at drafting to the card version live at the comment, or to a later version known at
drafting that changes only the name or the image. The quote and the receipt name that version and carry its image.**
No contract change: the order event keeps `receipt{card_id, card_version, image_url, price}`, `v` stays 1. The
`confirm_order` payload gains `image_url` and the `receipt` payload gains `sku` and `name`; message payloads are
free objects in `message_out.schema.json`. Chosen by the product owner on 31 October.

- Why: the worker adds the cut-out image in a version after the price, so a comment written between the two was
  locked to the version without an image and every such receipt had `image_url: null`.
- One function (`flow.locked_card`, called from `draft_order`) serves both order flows. The walk follows the
  versions one by one and stops at the first that changes the SKU, price, currency, variants or stock, or drops the
  name or image; such a change keeps the comment-time version as before.
- After the quote: a new price, a new SKU or a dropped variant sends the customer's yes to the seller
  (`card_change`; SKU is new). A new name or image does not, and the receipt keeps the quoted version.
- A locked version without a SKU waits for the seller, as the attachment flow already does. Without a price: the
  seller path as before. Without an image: allowed and counted (`with_image`). `orders/rules.py` is unchanged.
- Rejected: locking to the newest version at the customer's yes (the customer would confirm a version they were not
  quoted); locking at drafting to the newest version whatever changed (a new price or SKU would be quoted without
  the seller).
- Not done here: a price set in the console makes an `edited` version and queues no cut-out, so such cards still
  get no image (backlog, worker change). The acceptance wording for C5 is a separate contracts change.

## 2026-10-28 — attachment reference eligibility

**D-030 Clarify that frame coverage and reference resolution are separate prerequisites for ready.**
This preserves the existing reference kinds and C3 intent: current_card chooses current at comment media
time, previous_card chooses previous, and explicit_sku chooses only a unique real matching card within the
permitted frozen snapshot/evidence. Missing, ambiguous, out-of-snapshot and unresolved references do not
auto-select or fall back to current. The snapshot remains 0–2 current/previous cards in that order.

The stored referenced_card_id must be checked/reconciled with canonical history/snapshot and the proven
reference target before binding or materialization; it cannot confer authority or override the kind's result.
Incomplete reference resolution retains the original deadline and then follows expiry/seller choice, with
all original timing/reference/recall denominators. A valid selected card without SKU/approved price stays
fixed while waiting. Provisional first-candidate lines on the two-priced path are not automatic choices;
both seller decision paths preserve their explicit membership/session/version/price guards.

No event/request shape, enum, field, version or order state changes. Schema examples already permit ready
to name previous or another uniquely matching snapshot member; their base/head validation is structural,
not proof of cross-record eligibility or C3 runtime behavior. No wording-string tests or test-only resolver
are used. The future runtime must verify these guards with real history and the unchanged C3/ref criteria.
No price authority, prompt, catalog, gold, C1/C3/gate or customer-confirmation rule changes. This clarification
does not itself grant additional runtime or paid authority; product-owner review/merge is required.

## 2026-10-28 — proposed pre-order attachment history

**D-029 PROPOSED: preserve attachment outcome before an order can be priced, with a frozen real-card snapshot
and audited seller decisions. Product-owner approval is still required; this is not runtime authorization.**
New `comment_attachment` event type v1, envelope type entry and separate system/attachment-decision request schemas.
Full proposed semantics and validation limits are in `contracts/comment_attachment.md`.

- Initial version records ready/expired, actual decision time, original comment/label refs and deadline,
  history cursor and 0–2 real candidate card/version/appearance refs. It contains no price or fake order.
  Later versions append seller decisions without changing the outcome, snapshot or clocks. Late evidence
  cannot rebind the selected card, expand candidates or turn expired into ready.
- New producer semantics require the chosen card's own SKU and approved price before an order. Two eligible
  frozen candidates retain the D-027 order-choice path; missing SKU is not cured by price alone. Historical
  order shapes/nullability, state machine, permissions and rules remain unchanged.
- Proposed seller route before an order: select / keep_unresolved / reject with expected attachment version.
  Server supplies actor; the body cannot supply price, actor, snapshot or deadline. Keep appends audit history,
  unlike the D-028 null-SKU no-op. Once an order exists, only its existing approve/reject route chooses items.
- Logical version uniqueness, compare-and-swap, canonical same-intent retries and recovery from history are
  runtime invariants. A duplicate event ID alone is not sufficient. Recovery never resets a deadline or
  backdates timestamps, imports evidence after the original deadline, or claims a missed timing target passed.

Product decisions explicitly requested: an empty frozen snapshot can only be kept unresolved or rejected,
with no automatic rescue snapshot; a pre-order reject only closes the console intent and sends no customer
message or fake CANCELLED order. These effects are proposed for approval, not assumed accepted.

Proposed system ingress: one HTTP endpoint for an authenticated worker bound by durable server configuration
to the exact session/active epoch, with initial-outcome scope only. It submits source refs, observed cursor and
optional per-frame terminal evidence; the app derives outcome, clocks/deadline, snapshot and actor. Evidence is
frozen on ready outcomes. Server checks frame coverage, refs and posted cards; terminal reads remain worker
attestations, not independently observed execution. No boolean, identity or credential alone proves ready.
Before deadline missing proof stays pending, at/after deadline it expires using real time; late labels/requests
remain timing misses without reset/backdating or denominator exclusion. Generic ingest cannot forge events,
and worker authority cannot become seller authority. No credential, provisioning or runtime implementation
is included; rollout requires guards/readers/configuration before enabling this flow.

The proposed full-evidence bound covers the approved five-lap run: 351 frames per lap, at most 1755 terminal
records, two required card-post refs per frame and 1 MiB raw ingress/serialized ready event. A synthetic compact
maximum-identifier request is 846461 bytes. Over-limit requests fail closed without truncation or changing
workload/denominators; app expiry still uses the original deadline. No durable-prefix optimization or 4000-ms
runtime guarantee is claimed. App-owned expiry can record a null worker epoch without inventing a worker;
ready and ingress still require a real active epoch, and seller versions preserve the stored epoch/null.

Unresolved **SPEC_GAP**: D-028 cannot assign a SKU to an already-priced unidentified live/edited card. This
proposal does not widen that route or change its status; it remains pending/keep/reject until a separate owner
decision supplies a valid continuation. Rescore must count these stuck cases and report recall/seller-effort
impact; no sample is dropped and abstention is not counted as correct.

Validation is structural only. Tests do not prove actor authority, same-session membership, real references,
chronological relationships, immutable snapshots, CAS or recovery. Old events remain valid under the new v1
schema set; old validators/readers need coordinated updates before enabling producers. C1/C3/C5/C8, gold,
caps and paid gates are unchanged. No producer, route, order flow or UI implementation is included.

## 2026-10-28 — seller identifies an unresolved draft

**D-028 The seller may assign a catalog SKU to an unresolved draft, or explicitly leave it unresolved.**
Contract only: new `POST /api/sessions/{id}/cards/{card_id}/item` request shape in
`contracts/requests/card-item.schema.json`; the result uses the existing `card` event. No new event field/type,
order state or automatic permission; v1 is retained. Route implementation is pending.

- Body: required `card_version` and `sku` (a non-empty string or null). Only the seller console can make the
  choice. Body shape does not establish authority; customer text cannot grant it.
- Only the latest draft with no SKU is eligible. Runtime checks the card belongs to the session, its highest
  version matches the request, and a chosen SKU belongs to the session's active catalog, atomically with writing.
- A SKU appends version + 1 of that same card with catalog identity/variants/stock and `edited_by: "seller"`.
  It preserves frame/speech provenance, this card's own price and draft status, and cites the prior event.
  It never copies another item's or the catalog's price, rewrites history, confirms an order or sends a message.
- `sku: null` is an explicit no-op, after the same status/version checks: unresolved card, version and price
  stay as they are; no event, order or message. It does not reset attachment deadlines or assert readiness.
- Existing order pins and known-SKU cards cannot be reassigned through this route. Unknown SKU is 422; stale
  version, ineligible card or concurrent-write conflict is 409. Full response/check semantics are in the contract.

Why: category/colour can identify several catalog items. The seller must choose rather than deriving SKU
authority from a price, text or array order. Structural schema tests do not prove cross-event history,
catalog membership, permissions, concurrency or route enforcement.

## 2026-10-28 — current item, attached price and seller item choice

**D-027 Current item follows frame-time appearances; uncertain attachment lets the seller choose between two
known items. An order uses only the attached item's approved price and pins the version that supplied it.**
Contract only: optional `order.lines[].candidates`, new optional decision-body `card_id`, request schema and
contract wording. Existing required fields, order states and permission decisions stay unchanged; v1 is retained.

- Appearance = first non-null frame time of a card or a version with a changed frame time. Sort by
  `(frame_ts_ms, version)`; current at t is the latest appearance ≤ t and previous is the latest other card.
  Price, size, status and SKU edits keep frame time and do not create appearances. `/cards/live` retains its
  live-at-t membership but orders by last appearance, current first. A labeler may use later-known versions;
  a replay endpoint only uses versions written by t.
- At attachment, use the attached card's latest version when it is live/edited with an approved price. If it has none, no order
  is written yet; its next live/edited priced version supplies the price and is pinned. No price is written
  into an earlier version, taken from another card/customer text or changed on an already sent order.
- Optional `candidates` is exactly two distinct card IDs, current then previous at the comment's media time,
  using versions known at expiry. It is a frozen unresolved-choice snapshot, not permission to confirm.
- Seller approval must name one candidate through optional `card_id` on the existing decision route. Runtime
  checks membership, same session, order state and selected approved version; it pins the selected card's price,
  reruns the existing checks and records actor seller. It never carries the old line price to a different card.
  Without candidates, existing decision bodies work as before; a rejection makes no item choice.
- Only one line requiring candidate choice is covered by this body; selecting several lines requires a further
  contract decision. No new line index, event, state or assistant permission is added here.

Validation boundary: JSON Schema proves two distinct syntactically valid IDs and request shapes. It cannot
prove card existence, membership in a stored session/order, price/version provenance, actor authority or valid
transitions. Those checks and the route/worker/order/UI changes require separate runtime implementation and tests.
Older strict event validators need the new optional field before accepting candidate-bearing events.

Open **SPEC_GAP**: expiry with a missing approved price or fewer than two known cards cannot be represented by
the existing required order line plus this two-candidate field. No placeholder zero, prior-item price,
nullability change, delayed deadline or invented readiness is authorized. A persisted ready/expired outcome
between attachment and the order consumer is also undecided; `comment_labeled` is unchanged. Both require an
owner decision before implementing those cases. This contract alone does not open all remaining runtime gates.

## 2026-10-28 — CATALOG_PATH and category lists

**D-026 `CATALOG_PATH` changes only the items on sale; the vision and speech prompts keep the words of `data/catalog.json`. An item may list `categories`, and a label plus colour that names exactly one SKU opens that SKU's card.** No contract change: no new field or event, no API change, `v` stays 1. Coordinator decision, notes 417 and 423.

The coordinator's decision, quoted (the two role names generalised):

> "D-026 đêm nay thu hẹp: (1) CATALOG_PATH chỉ đổi dữ liệu món/size/tồn/giá/tên và mục catalog trong prompt nhãn; từ vựng vision và màu bộ đọc giá giữ từ catalog demo; test hash prompt vision và prompt giá không đổi so với main. (2) Category list theo membership, tương thích ngược. (3) Khớp SKU: nhãn+màu ra đúng một SKU → gán; còn lại → NEEDS_SELLER như hiện tại. Không thêm trường nào vào event/API, không contracts. (4) Trong measure/NOTE: phân rã đơn NEEDS_SELLER theo 'giá thẻ khớp đúng một giá catalog' (would-be candidate) / không, tính ngoại tuyến từ event. Một người viết, người kia review, vào main trước 13:00. Candidate UI/contract để Founder quyết ban ngày."

- `CATALOG_PATH` (`app/settings.py` `catalog_path()`): a path relative to the repo root, or absolute; unset or empty means `data/catalog.json`. It is read once, when `worker/brain/card.py` is imported and when a `Labeler` is built.
- From `CATALOG_PATH`: `card.CATALOG` (`match`, `item_by_sku`, `variants_of`, so the SKU, name, sizes, stock and price on a card) and the catalog in the label prompt (`labels.load_catalog`).
- From `data/catalog.json` whatever `CATALOG_PATH` says: `card.CATEGORIES` (the vision category enum), `card.COLORS` (vision prompt, speech prompt, colour filter, `vn.parse`) and `card.keywords()` (the transcriber's keyword list). `tests/test_catalog_path.py` holds the sha256 of the vision prompt and schema, the speech prompt for a fixed sentence and the keywords as on main 86264ba, and checks them with `CATALOG_PATH` unset and pointing at another catalog.
- Membership: an item with `categories: [..]` is of each category in the list; an item without it is of its `category`, as before. `match` keeps its rule: the items of the seen category, then those with the seen colour when one is seen; exactly one → that item, else none.
- No SKU or two or more: the path stays as before. The card is a draft with no SKU, a name from the category and colour, no variants and stock 0; an order on it goes to NEEDS_SELLER (no price yet, or qty above stock 0).
- (4), the offline split of NEEDS_SELLER orders in `measure/`, is not in this change.

Why: the coordinator's note 417 chose one catalog file per run, with category lists. The C5 catalog gives its items a `categories` list; matching on `category` alone leaves an item seen under another of its categories without a SKU. Keeping the prompt words fixed keeps the vision and speech prompts the earlier runs were measured with.

Known limits: the vision enum and the speech colours do not grow with `CATALOG_PATH`, so a category or colour only in that catalog is never seen or heard. A spoken price is not matched to a SKU (coordinator note 420, open). Restart the app and worker after changing `CATALOG_PATH`.

## 2026-10-28 — which card a spoken price goes to

**D-025 A final speech line goes to a card by media time; when several cards are in view, the kind of item it names picks one. Any other heard price waits for the seller.** No contract change: no new field or event, `v` stays 1.

- Span: each `is_new` product_seen opens a span on its card, `[frame_ts_ms − D, the next is_new product_seen's frame_ts_ms)`, with D = 1000 ms because frames are cut at 1 fps (D-021). A card reopened by SKU gets another span.
- Candidates: the cards that are not retired and have a span overlapping the line's `[start_ms, end_ms]`.
- Price: it goes to a card only when the line has a magnitude word (nghìn, ngàn, trăm, triệu, củ; k or tr right after a number) and exactly one candidate fits every kind it names (every candidate fits a line that names none). So with one card in view a line sets its price without a kind word; with two or more, a kind word has to pick one. On that card, no price → the price and live; another price → a proposal (D-024); the same price → nothing.
- Kind words: giày and đôi → shoes; áo → tops; tay cầm and bàn phím → `other` (the vision category for items outside the catalogue). From the catalogue names, with no test1 line using them: túi, thắt lưng, váy, mũ, quần, dép, sandal, boot.
- Otherwise the price is not applied. The card keeps "giá chưa có" and the console's price box, and the worker logs `price wait: <speech id> reason no_magnitude|none|many candidates <card ids>; session <session id> waits <reason>=<n> …`, without the text. The worker counts waits by reason per session; the last `price wait` line of a session in the worker log holds its totals (for C5).
- Sizes: applied when exactly one candidate fits the line's kind words (every candidate fits when it names none), also when the price waits.
- Hold: a line is decided at max(read done, received + HOLD_MS), so a product_seen still being read can arrive first. HOLD_MS = D + H, and H = 3168 ms is the ledger p95 of product_seen latency in run probe-test1-6b-1950 (351 frame reads at the current prompt and effort, `worker/vision/spark.py`). Lines are decided in arrival order; their reads run in parallel.
- Retire: at a switch, the previous draft is retired only when no undecided line overlaps its spans (this replaces the count of reads in flight). After each decision, an overlapping draft that is not current, has no price and has no other undecided overlapping line is retired by that speech.
- Speech prompt: the card sentence is gone and "của món này" is now "được nói trong câu", because the line is read before it is known which card it goes to. Both prompts read 22/22 gold prices (`docs/evidence/2026-10-27/speech64-card-line-NOTE.md`).

Why: a price said around a switch went to whichever card was current when the line arrived. In the D-024 replay of test1, 7 proposals carried a later product's price on an earlier card.

Changed on 2026-10-28 (coordinator decision): a kind word picks between several candidates and is no longer required. In the 0 USD replay of test1, requiring it left 3 of the 6 right-card proposals waiting.

Known limits: a price said before the session's first product_seen is not read. H holds only for the current vision prompt and effort, and was measured on files, not the live path; re-measure when either changes. Each line now waits up to HOLD_MS (4168 ms) before it is applied. A line that names no kind of item goes to the one card in view, also when the seller is talking about an item not shown yet. The wait counts live in the worker's memory and log only, and restart with the worker. The kind words cover the catalogue names and the words used in test1 only.

## 2026-10-27 — price proposals and the console price route

**D-024 A different spoken price on a priced card becomes a `price_proposal` for the seller; a seller price reaches the card only through a console route.** Contract change: one new event type, and two refusals at `POST /api/sessions/{id}/events` that no current producer triggers (in the seed sessions no priced card changes price and no `(card_id, version)` repeats); `v` stays 1.

- New event `price_proposal` (`contracts/events/price_proposal.schema.json`): `card_id`, `card_version`, `current_price`, `heard_price` (1000…100 000 000, which covers both speech paths: the rule reader takes 10 000 and up, the model reply 1000 and up), `speech_event_id`. `caused_by` is required and lists the speech. Added to the envelope `type` enum.
- Who said it: `speech` carries no speaker identity, so the proposal names the speech event; there is no speaker field. When: the envelope `ts_ms` is the time of the speech.
- New console route `POST /api/sessions/{id}/cards/{card_id}/price` (`contracts/README.md`, "Card versions"): writes `card` `version + 1` with status `edited` and `edited_by: "seller"`; the same price writes nothing and answers 200 before the version check, so a repeated tap is safe; 404, 409 and 422 as listed there.
- `(card_id, version)` is unique in a session: the store refuses a card version that already exists (409), and the writer reads the latest version and applies its change on top, or drops it.
- `POST /api/sessions/{id}/events` refuses (409) a `card` whose price differs from the card's latest non-null price. A priced card changes price only through the route, which writes through the store.
- A proposal is open while the card is not retired, its latest price equals the proposal's `current_price`, and no seller version of the card is newer than the proposal's `card_version`. There is no dismiss event; a new seller price closes older proposals, also when the price later returns to the old value.
- Proposals appear in the console only. The overlay and every customer message stay as they are.
- The console treats "a seller version newer than `card_version`" as any newer version whose price differs from `current_price`. The two are the same here. Only the route changes a priced card's price, worker versions copy `edited_by` from the version before, and a same-price seller tap writes nothing.
- Replay of test1 at 0 USD (rule reader, no key; `docs/evidence/2026-10-27/price-proposal-replay-NOTE.md`): 11 proposals in total. The 3 real changes give 2 proposals each. SP01 and SP04 land on the card that holds their first price, and SP09 lands on SP06's card. 7 proposals carry a later product's price on an earlier card. There is no threshold.

Why: after D-023 the shop's real price changes (3 in the test1 transcript) stay at the old price, with no way to apply them. This gives the seller one tap to apply a heard price (C4), while speech still never changes a priced card.

Permissions: `contracts/permissions.json` does not change. The route is a seller action in the console, like the card edit in C4; a separate permission row for it is not part of this change. As with the order routes, anyone with console access decides; there is no seller identity.

Why the two refusals: without them `POST …/events` could change a priced card's price outside the route, and a worker bump and a route write could take the same version, so an order pin would match two cards and the later `ts_ms` would hide the seller price (found in review of PR #19).

Not in this change: the worker that emits the event, the route code, the two refusals in code and the console UI (they follow in separate PRs with tests); a dismiss button.

Known limit: a worker version written just after a seller version can carry an earlier `ts_ms` (its speech or frame time). "Live at t" orders versions by `(ts_ms, version)`, so from the seller's `ts_ms` on, the seller version stays live and that worker version's other changes (sizes, colours) show only from the next version. Between that earlier `ts_ms` and the seller's, the worker version is live, so the seller price applies a moment before the tap. The price stays the seller's, because the worker version copies it and anything else is refused.

## 2026-10-27 — spoken price on a priced card

**D-023 Speech never changes the price of a card that already has one.** A spoken price sets the price only on a card whose price is null, as before: the draft goes live. On a card with a price (live or edited), a different spoken price changes nothing on the card. `worker/brain/card.py` logs one `price proposal` line with the card id, version, current price, heard price and speech event id; the spoken text is not logged. Sizes and colours from the same speech still apply.

Why: on the test1 mini probe replay (0 USD), all 5 times speech overwrote a price, it was the next product's price landing on the previous card (320→280, 180→150, 210→450, 450→550, 550→650). After this change those cards keep their first price.

What it costs: the test1 transcript has 3 real price changes by the shop (SP01 250→220, SP04 180→160, SP09 650→600). They now stay at the old price until the shop edits the card, and there is no console route to edit a card's price yet. That route, and a place to keep a proposal (a new event or card field), need the contracts owner and the Founder; this change adds neither. Orders stay safe: a customer price different from the card goes to `NEEDS_SELLER` (`contracts/permissions.md`).

Not covered here: attaching a spoken price only when the product is unambiguous (a price spoken before its product_seen still lands on the current draft).

## 2026-10-27 — label latency at live speed

**D-022 Labels at minimal effort miss C8 (label p95 ≤ 1 s); the target stays.** `label_comments` now calls Spark with `reasoning.effort=minimal`, the lowest Spark accepts (`none` is refused), and a shorter output schema: 8 required fields, with contact signals as one enum (`none`, `phone_number`, `link`) instead of an optional list. On 200 synthetic comments posted at live speed (`connectors.sim.comments --speed 1`, session `comments200-s1-1027-123307`, 199 calls, $0.2578, 0 errors):
- End to end, comment stored → model label stored: p50 3007 ms, p95 3895 ms (max 10210, one slow call).
- About 1.0 s of that is the batch window. At live arrival rates a batch holds one comment (198 of 199 calls), so `BATCH_WAIT_S` is pure wait.
- About 2.0 s (p95 2.9 s) is the model call itself.
- Template-gold F1 0.976 on n=170 (0.982 at default effort); order fields 71/71 exact; scams with contact hidden 26/26.
- Before: sim speed 20, default effort, batches of 6–9, label p95 21.4 s.

The target in `contracts/acceptance.md` is not changed. Contact comments are still hidden on receipt by the shield, at p95 22 ms. Not done, open for the Founder: send a batch at once when the worker's queue is idle instead of waiting 1 s. That removes about 1 s and still leaves p95 near 2.9 s, above the target.

This supersedes D-011 for the 200-comment set (built in `data/comments/`); the 500-comment set is still not built.

## 2026-10-27 — live product cards (worker/media, vision, voice, brain/card)

Numbers continue after D-014/D-015 (PR #4) and D-016 (PR #6).

**D-021 Frames, Spark latency, stream wiring.** Frames are cut at 1 fps with the long side at most 640 px (D-015: phone portrait 1080×1920 → 360×640). Spark frame reads with the current `meta/client.py`, measured on synthetic frames: p50 4782 ms / p95 6233 ms (n=20); card_from_speech 5–8 s. `worker/brain/__init__.py` merges each stream's HANDLERS per event type with one extend-safe line, so other streams append without editing card code. Events from `worker/media` (frame, speech) use `source: "replay"` because they come from a recording, and carry the four C8 timing keys on the session clock.

**D-020 The card image is the frame cut to SAM's box with SAM's mask as alpha.** SAM's reply shape is in D-014; `meta.client.parse_segmentation` reads it. The mask is decoded with `meta_sam_parser.decode_mask_to_raster` (Meta's package, pinned at 0.1.0, no runtime dependencies; added on the Founder's order of 27 October). What we found:
- The decoder returns one byte per pixel, 0 or 1 (lossless coverage ≥ 129 counts as 1), row-major with the top row first. `parse_segmentation` drops the payload's `~`/`!` marker, so `worker/vision/sam.py` puts it back before decoding.
- The mask is box-local: `x=0;y=0` and H×W equal the box size (corners inclusive). Meta's own parser accepts only `x=0;y=0`, so we do the same; a mask that is not box-sized, has other x/y, or does not decode leaves the plain box crop (background kept) and a log line.
- The real reply for `meta.probe.test_image()` (200×160 red box on grey 640×480) decodes to 160×200 with 99.9 % foreground in 87 ms; its output text is `tests/fixtures/sam_red_box.txt`.
- One real call on a synthetic portrait frame (1080×1920 scaled to 360×640, a red right triangle with the right angle bottom-left): a 158×158 cut-out, 50.9 % opaque, top row 1/158 opaque and bottom row 156/158, left column 158/158 and right column 3/158. This confirms the row and column order on a real mask. 1151 ms, 0.0025 USD.

The cut-out is the frame's own pixels: the tests check that every RGB value equals the plain box crop's. Only the alpha channel is added, as a PNG data URL at most 360 px wide (the PNG is never upscaled). No box → `image_url` stays null.

**D-019 Voice runs in ENDPOINTING mode; one `speech` event per completed turn.** The handshake and message types are in D-014 (`meta/client.py`, PR #4; this branch does not change the client). `worker/voice` sends `mode: "ENDPOINTING"`, the catalog as `keywords` and `languageBias: ["Vietnamese"]`. Only `speechComplete` becomes a `speech` event; its span is the turn's `speechStart` → `speechComplete` `audioProcessedMs` (processing progress, not word times). An `error` message ends the stream. Real run on 300 s of synthetic Vietnamese speech (macOS TTS, 15 seller lines looped): 67 final turns, final-after-turn-end p50 1422 ms / p95 1504 ms (n=67), 0.015 USD. The 5 turns printed match the script except one doubled word, and one turn joins two lines; word error rate was not measured.

**D-018 Speech is read in the background.** card_from_speech takes 5–8 s on Spark, and the worker handles events one at a time, so an inline read delayed the next `product_seen` card by up to 2.4 s on demo1. `on_speech` now pins the card that is current when the speech arrives and returns at once; the read finishes as a background task and posts its version then. A card with a read in flight is not retired by a product switch; if the read brings no price after the switch, the card is retired then. Cost: a card goes live about 5 s after the price is spoken, against ~0 ms for the rule reader alone.

**D-017 Replayed product_seen needs a flag to open cards.** A `product_seen` without `caused_by` comes from a replay file, not from the vision worker. It opens cards only with `CARDS_FROM_REPLAY=1`, so a replay that still carries fixture cards does not get a second set. With `--no-fixtures` the vision worker makes `product_seen` from frames and sets `caused_by`.

## 2026-10-27 — first real calls, input format

**D-016 Tests and smoke use a separate `_test` database; the test rows already written stay.** Until now `test_pg_store_append_only` and `make smoke` wrote to the project database, and a labels worker running live on that database labeled one test comment for real: one billed Spark call, 0.003589 USD, session `pytest-pg`, 27 October. At 12:34 the project database held 566 rows from test sessions (`pytest-pg` 14, eight `smoke-*` 69 each); the log is append-only, so they stay, and branches without this change keep adding to them until they merge it. From now on:
- pytest and smoke use `TEST_DATABASE_URL`, else `DATABASE_URL` with `_test` added to the database name (`keviq_test`), created and migrated on first use (`python -m app.migrate --test`). pytest exits with code 1 before any test runs if that name does not end in `_test`; smoke and `migrate --test` refuse the same way.
- pytest and smoke run with `MODEL_API_KEY` set to empty, so no test makes a billed call (the voice tests set a fake key of their own).
- `/api/cost` adds `test_sessions` (session ids starting `pytest` or `smoke-`): today's and total spend caused by test sessions. The caps still count every `model_call`, test or not: the 0.003589 USD was billed, and a cap that left it out would sit below the real bill.

**D-015 Phone portrait is the default input.** The live source is a phone stream, 9:16 (1080×1920). The frame cutter, product card and overlay take portrait as the default; landscape from OBS is a later, secondary case. The Founder's test video goes in `data/local/test1/` (portrait). Spark's "640 px" frame means the long side (360×640, same token cost as planned); if product recognition on test1 misses the gate, try 720×1280 and record the cost next to the result.

**D-014 Model API shapes checked with one real call per model (closes D-007).** `python -m meta.probe` on synthetic inputs (an ffmpeg-drawn image, 10 s of macOS speech), 27 October, three `model_call` events in session `probe-20261027`:

| model | tokens in/out | cost USD | latency ms | units |
|---|---|---|---|---|
| muse-spark-1.3 (structured output, reasoning minimal) | 415 / 111 | 0.000991 | 2072 | — |
| sam-3.1 (one image) | 0 / 0 | 0.002500 | 696 | images 1 |
| muse-voice-transcribe-1.0 (ENDPOINTING, 10 s) | 0 / 0 | 0.000500 | 20842 (whole stream) | audio_seconds 10 |

What the replies showed, and what `meta/client.py` now does:
- Spark: `output[]` is a `reasoning` item (no content) then a `message` with `output_text`; usage has `input_tokens_details.cached_tokens` and `output_tokens_details.reasoning_tokens`. Reasoning tokens are inside `output_tokens` and billed. `reasoning.effort` is a parameter of `responses()`.
- SAM 3.1: input is a `message` with `input_text` + `input_image`, `stream: false`, metadata `include_confidence`, `mask_encoding`. The reply is text in the `output_text` lane, not images: `<0f>0<|box;x1;y1;x2;y2;w;h;c|><|mask;x;y;c;data=H,W,~payload|>`. Box corners are inclusive pixels, box w/h are the image size, mask H×W is the box size. `usage.images_processed` is present. `parse_segmentation()` returns the records; mask pixels are not decoded in `meta/` (the reference decoder is `meta_sam_parser`, not yet a dependency; decoding is unverified).
- Voice: the key goes in the first JSON frame (`authorization.accessToken`), not a header; `audioEncoding` PCM_24KHZ; `languageBias` takes names (`Vietnamese`), not codes; audio frames of 80 ms paced at real time, then `{"type": "endStream"}`. Sequence seen: `{sessionId}`, `speechStart`, 24 cumulative `transcript` partials, `speechEnd` and `speechComplete` (turn transcript, at 8140 ms of audio), then an empty `transcript` with `final: true` at end of stream. The latency above is the whole session (10 s of real-time audio plus close), not speech-to-text delay.

Tests: `tests/test_meta_shapes.py` replays these shapes offline (fail on the previous client, pass now).

## 2026-10-27 — scaffold + contracts

**D-013 Acceptance criteria in English (v1).** `contracts/acceptance.md` is replaced by the Founder's English version of C1–C11 (27 October 2026); the Vietnamese copy of the day-1 table is retired. Changes in form: the "changed from v0.2" column becomes "how it is checked", the out-of-P0 row becomes a paragraph, and the C11 sourcing note is shortened. The criteria and targets are the same; no schema, measure or test change follows from it.

**D-012 Rules boundary with the orders stream.** `orders/rules.py` holds the permission table (mirrors `contracts/permissions.json`, a test checks they are equal), `authorize()`, the 24 h message window and `evaluate_draft()`. The orders stream extends `evaluate_draft()` and keeps the `Decision(allowed, needs_seller, reason)` shape; any change to `rules.py` comes with a test in `tests/test_rules_invariants.py`.

**D-011 Labeled comment sets not created yet.** The 200 and 500 labeled comments for C3/C5 (`data/comments/`) are not in the scaffold. Today `measure/` runs on demo1 (14 comments, 7 orders); its C3 line prints the target n=50 next to the real n so the gap stays visible.

**D-010 C8 display stage is approximate.** `display = ingested_ms - timing.emitted_ms` (worker emit to app store). Console render time is not measured yet; the README must say so next to any published C8 number.

**D-009 C1 interpretation.** Consequence of trigger T = an event listing T in `caused_by` whose arrival time is within 0..5000 ms of T's arrival (`when()` = `ingested_ms`, else `_replay_at_ms`, else `ts_ms`). Triggers: every `product_seen`; every `comment_labeled` whose label is order, price_question, stock_question or complaint. A label with action hide or warn is its own consequence; `other` with action none needs none.

**D-008 Replay file conventions.** `ts_ms` (and `frame_ts_ms`, `start_ms`, `end_ms`) in a session file are relative; replay rebases them to `start + rel / speed` and namespaces every `event_id` (and `caused_by`, `speech_event_ids`) as `<target>.<id>` so one file can be replayed many times. Optional `_replay_at_ms` = arrival time for late comments; stripped before posting. Event ids starting `fx-` are hand-written stand-ins for worker output; `--no-fixtures` drops them so the real worker produces those events. Derived event `ts_ms`: card = version emit time, comment_labeled = the comment's time, order = transition time.

**D-007 Model API shapes not verified against the live API.** Base URL, model names, auth header and prices are from dev.meta.ai docs (read 2026-10-27). The structured-output request/response shape, SAM input shape and the voice WebSocket protocol are written from the docs but not yet called for real, so parsing is defensive (`output_text` or `output[].content[]`, two token-count spellings). First real call per model: check the shape, fix `meta/client.py`, add an entry here.

**D-006 Cost cap is enforced in our code.** The Billing page limit only notifies. `Meter.check()` runs before every call: today (VN day) ≥ 30 USD or total ≥ 300 USD → `BudgetExceeded`, a `model_call` with `ok=false, error=daily_cap|total_cap` is logged, no request is sent. At 20 USD/day a warning tells the Founder. Spend is summed from `model_call` events in PostgreSQL (`/api/cost`). Muse Wonder is refused in code.

**D-005 Local PostgreSQL on 127.0.0.1:55433, bridge network.** Port 55432 was taken on the dev machine and Docker's address pools were exhausted, so compose uses `network_mode: bridge` instead of a project network. Override with `DATABASE_URL` in `.env`.

**D-004 `seq` has gaps.** `seq` is a global BIGSERIAL and the SSE cursor; `INSERT … ON CONFLICT DO NOTHING` consumes a value on duplicates. Consumers must treat `seq` as ordered, not contiguous.

**D-003 One writer.** The app is the only process that writes events. Replay, worker and connectors are HTTP clients (`POST /api/sessions/{id}/events`, SSE `GET /api/events?cursor=`). Ingest is idempotent on `event_id`; the events table is append-only (trigger rejects UPDATE/DELETE).

**D-002 Envelope additions.** Beyond `event_id, session_id, ts_ms, source, v, type`: optional `caused_by[]` (provenance, C1/C6) and `timing{received_ms, infer_start_ms, infer_end_ms, emitted_ms}` (C8). The server adds `seq` and `ingested_ms` on read; they are table columns, not fields of the event body. Schemas inline the envelope and set `additionalProperties: false`.

**D-001 Per-session keys.** `card_id`, `comment_id` and `order_id` are unique within a session; only `event_id` is globally unique.

Text edited 2026-10-29: wording only; sha256 before 148b796f741039cb44b079be15271d4585fb5cc684fbbcf613ee362e7097ea89 after c675e9479f23c5914094440173e84b11b318b0d431c25103803756fc6e48a635
