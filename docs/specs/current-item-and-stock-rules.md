# Current item, stock hold, stock replies (spec)

Status: spec only. This change touches no code, contract or runtime. Rule 1 is coded in a PR on 29/10. Rules 2 and 3
come after the 30/10 gate. Rule 2 and the end-of-live event change order states, so they go through a contracts PR
that the contracts owner and the product owner review and the product owner merges. Line numbers below are on main
`fa52ad8`.

The afternoon session of 28/10 measures the baseline before this fix and says so in its NOTE: "baseline before the
current-item fix".

## The rules

The product owner set these rules on 28/10:

1. **Current item.** A comment's item is the item shown most recently at the comment's time, by the frame time
   (`frame_ts_ms`), not by when its recognition came back. The appearance history is kept by frame time; a price or
   size edit is not a new appearance. The worker tracks how far frames have been handled. A comment at t that comes
   before its frames are handled waits in a queue bounded in time (the bound is the measured recognition latency)
   and is then attached to the item current at t. If it is still unclear at the deadline, the order goes to the
   seller with two candidates for one tap; the customer is asked only for a missing variant, as today. An order
   never takes the previous item's price, and no price is written into an earlier version of the new item. If the
   new item has no approved price, the order waits for it before the confirmation is sent. `/cards/live`, the
   labeler and the UI use this one definition. Four tests are required (below).
2. **Stock hold.** Stock is held from `CONFIRM_SENT`, as today. It is released 10 minutes later or at the end of
   live, whichever comes first, and on cancel. A receipt is sent only in `CONFIRMED`.
3. **Stock replies.** A stock question is answered only when the item and the variant are certain, with the number
   from the stock data. Otherwise there is no answer.

Decisions on the points this spec first left open:

- **Approved price.** The price on the card's live or edited version: set by speech once the line is decided after
  `HOLD_MS` (D-025), or set or edited by the seller. A price that is not on such a version, such as a
  `price_proposal` (D-024), does not count. The order waits for that live version before the confirmation route.
- **No back-writing.** Card versions are append-only. A waiting order cites the later priced version it was drafted
  from.
- **End of live.** The seller ends the live in the console, or the media has stopped for 5 minutes. An event records
  it with actor and reason. That event is scope for the contracts PR after the gate and is not implemented now.

## 1. Current item

### Today

There are three orderings, and none of them uses the frame time:

| Where | Current card at t | Used for |
|---|---|---|
| `app/store.py:63` `live_cards_from` | Live/edited cards at their latest version with `ts_ms ≤ t`, sorted by the `ts_ms` of each card's **first** version, newest first. A card shown again does not move back to the top. | `GET …/cards/live` (`app/main.py:131`, `contracts/README.md:43`), `/state` `live_cards` |
| `worker/brain/labels.py:118` `cards_at` | `live_cards_from(…)[0]`; previous = the other card with the latest first version | model context (`:297`), `resolve` (`:133`), `ask_which_item` options (`:364`) |
| `app/static/console.js:42`, `app/static/overlay.html:21` | Sorted by the latest version's `ts_ms`, so a seller price or a speech version moves a card to the top; the overlay also shows drafts | console list, overlay |

A card version's `ts_ms` is the time the version was written (`worker/brain/card.py:183` `partial`, `now_ms()`). That
is after the frame read, whose p95 is H = 3168 ms at the current vision prompt and effort (D-025). So "live at t" is
what had been recognised by t, not what was on screen at t. PR #32 (draft) changes only the labeler's ranking. It
waits for this spec and is not merged today.

### Definitions (one, shared)

- **Appearance**: a card version whose `provenance.frame_ts_ms` differs from that card's previous version, the first
  version included. Its time is that `frame_ts_ms`. Today only `on_product_seen` writes a new `frame_ts_ms`
  (`card.py:208`, `:226`). Speech versions (`card.py:376-377`), seller prices (`app/cards_api.py:93`) and status
  bumps (`card.py:135`) copy the provenance, so a price, size or status change is not an appearance.
- **Appearance history**: every appearance in a set of card versions, sorted by (`frame_ts_ms`, `version`). It is
  built from card versions alone, with no new field and no new event.
- **Current item at t**: the card of the last appearance with `frame_ts_ms ≤ t`. The card's status is not part of
  the rule. **Previous item at t**: the card of the last earlier appearance that names another card.
- One function computes both, `current_at(versions, t) -> (current, previous)`. Proposed place: `app/current.py`.
  The caller passes the set of versions:
  - The labeler passes every version it has when it attaches a comment. That is the point of the wait.
  - `GET …/cards/live?ts_ms=t` passes the versions written at or before t, as its members are chosen today (live or
    edited at the latest version with `ts_ms ≤ t`). It orders them by their last appearance at or before t, so the
    current item comes first when it is live. A replay of `/cards/live` at t shows what was known at t.
  - The console passes every version it has and orders its list by last appearance. The overlay shows the current
    item at now.
  - So the labeler and `/cards/live?ts_ms=t` can answer differently for the same t when a version was written after
    t. The rule is the same; the labeler's input is later and fuller.
- The console and overlay read one small JS copy of the rule (`app/static/current.js`). The repo has no JS test
  runner, so the JS copy is checked against the Python test timelines in the browser (L2.5), not in `make check`.

### When a comment is attached

- A frame counts as **handled** when:
  - its read has finished, with or without a `product_seen`, or it was skipped on an error; and
  - the card version its `product_seen` causes has been posted.
- A comment at t is **ready** when every frame with `ts_ms ≤ t` has been handled and a frame with `ts_ms > t` has
  been received, so no frame at or before t is still to come. It does not wait for the read of a frame after t.
  Frames are cut at 1 fps (D-021).
- This state lives in the worker's memory. It is not an event, so the labeler and vision must run in one worker
  process, as `worker/__main__.py` runs them today.
- **Order comments and stock questions wait; other labels do not.** The model is called at once, as today, with the
  cards known at the time. Only the attachment to a card waits: through `resolve` and the order flow for an order,
  and for the stock reply of rule 3. The shield and the labels of questions, spam and scams are not delayed.
- A ready comment is attached at once to the current item at t. Otherwise it waits in a per-session queue and is
  attached as soon as it is ready.
- **Deadline**: `ATTACH_WAIT_MS` after the comment is received. It is the measured p95 from frame time to its card
  version on the live path, is held in one place and is re-measured like H in D-025. The queue is bounded in time
  only.
- **On expiry**: the order goes to `NEEDS_SELLER` with two candidates, the current and the previous item at t on the
  versions known at that moment. The seller taps one; the customer is not asked. The customer is asked only for a
  missing variant, as today (`orders/flow.py:327-336`, `ask_variant`). An expired stock question gets no reply.

### Price

- The order is priced from the attached card only, never from the previous item's card.
- If the attached card's latest version when the comment is attached is live with a price, the order uses it and
  pins that version (pinning as today).
- **If it has none**:
  - no `order` event is written yet (an order line needs `unit_price`); the labeled comment waits in the flow;
  - when the card gets a live priced version, the flow drafts the order from that version, pins it, runs
    `orders/rules.py` unchanged, and sends the confirmation;
  - if the card is retired with no price, the order goes to `NEEDS_SELLER` ("no price"). Until the end-of-live event
    exists, the end of the session does not end the wait; the console shows the waiting comment.

  The waiting set can be rebuilt after a restart: it is the order labels with no order whose card has no price.
- A price never goes into an earlier version. The waiting order cites the later version, so the receipt carries that
  version. An order already sent is not repriced by a later version (pinning, as today).
- Today, an order on a card that is not live goes to `NEEDS_SELLER` (`orders/flow.py:316-318`, D-026). Under this
  rule it waits for the price instead.
- **C1.** A comment that waits for a price has no consequence event until the order is drafted, so it misses C1's
  5 s (D-009). This spec adds no event for the wait. C1 is reported as defined, with these comments counted as misses
  and also listed apart; an order event for the wait is a candidate for the contracts PR.

### Contract wording this needs (contracts PR)

- `contracts/README.md:43`: "live at t" stays as it is for a card's state. Add the current item and its ordering
  by appearance. `/cards/live` is ordered "current item first" instead of "newest first".
- `contracts/README.md:42`: the order price becomes "the price of the attached card's latest live version when the
  comment is attached; when it has none, its next live priced version".
- `NEEDS_SELLER` with two candidates: an order line holds one required `card_id`
  (`contracts/events/order.schema.json`), and the decision route takes only `decision` and `unit_price`
  (`app/orders_api.py:22-24`). Proposal:
  - an optional `candidates: [card_id, card_id]` on the order line;
  - an optional `card_id` in the decision body, which must be one of the candidates;
  - both are optional, so `v` stays 1.
- A `docs/DECISIONS.md` entry for each of the above.

### The four required tests

The times are media seconds. "Written" is the time a card version is posted. The tests set
`ATTACH_WAIT_MS = 4000` and feed the frames, reads and versions at the times given. A, B and P are items with
prices 150 000, 200 000 and 120 000.

1. **Quick switch.** A is live at 150 000 (frame 10.0). B's frame is at 12.0, under 3 s later (C7); B's first version
   is written at 16.0 as a draft with no price. Frame 13.0 is received at 13.0. An order comment arrives at 12.5.
   - It is ready at 16.0 (frames 12.0 and earlier handled, frame 13.0 received) and is attached to B.
   - No `order` event is written while B has no price.
   - A speech line is decided at 18.0 and B v2 is written live at 200 000.
   - Expected events: `card` B v2, then `order` (`DRAFT`, line on B v2 at 200 000, `caused_by` the label and B v2),
     then `CONFIRM_SENT` and the confirmation `message_out`. No order line ever cites A.
2. **A → B → A.** A is live at 150 000 (frame 10). B's frame is at 20 and a speech line makes B v2 live at 200 000
   (written 22.0), so B is not retired when A comes back. A is shown again at frame 30 (A's version written 32.5).
   - A comment at 25 is attached to B: `order` on B v2 at 200 000.
   - At 31, current = A and previous = B.
   - The seller sets B's price to 210 000 at 33 (B v3, edited). It is not an appearance: B v3 keeps frame 20.
   - A comment at 34 is attached to A: `order` on A's latest version at 150 000.
   - `/cards/live?ts_ms=34000` lists A first, then B; the console lists A first.
3. **Price spoken before the card opens.** P is live at 120 000 (frame 5). A line "price 150 000" ends at 9.5 and is
   received at 10.0. A's frame is at 10.0, its `product_seen` arrives at 13.8 and A v1 is written at 14.0. The line
   is decided at 14.2 (received + `HOLD_MS`) and lands on A through the D-025 span `[frame − 1000, …)`: A v2 live at
   150 000, written 14.3.
   - An order comment at 11 is ready when frames 10.0 and 11.0 are handled (11.0 read done at 14.5) and is attached
     to A: `order` on A v2 at 150 000.
   - Variant: A v2 is written at 16.0 instead. The order waits (no `order` event at 14.5) and is drafted on A v2 at
     150 000 after 16.0.
   - No order line ever cites P or 120 000.
4. **Comment right at a switch.** A is live (frame 15), B is live at 200 000 (frame 20.0).
   - Fast case: every read is done by 23.0. Comments at 19.6 → A; at 20.0 and 20.4 → B.
   - Slow case, with only the comment at 20.4: the read of frame 19.0 ends at 25.0; frame 20.0 is handled at 23.0.
     The comment expires at 24.4: `order` in `NEEDS_SELLER` with candidates [B, A] (current and previous at 20.4 on
     the versions known then). The seller taps B: the order line is on B at 200 000 and the confirmation is sent.
     The customer gets no question.

Each test is red on main before the fix. A 0 USD replay of test1 is run before and after the fix, with the
afternoon session as the baseline. It reports reference correct, proposals on the right card, the `NEEDS_SELLER`
count, the time from a comment to ready (p50/p95) and the expiry count.

### Known limits

- The frame grid is 1 s. A comment less than one frame before a switch attaches to the earlier item.
- At expiry, if the slow frame is the switch frame itself, the right card may not exist yet, so it is not among the
  two candidates. The seller can then reject.
- A wait adds up to `ATTACH_WAIT_MS` to the end-to-end time of order comments (C8). C8 is reported with and without
  the wait.
- The worker handles frames in one loop, one after another (read from the code, not measured). If reads fall
  behind the 1 fps grid, the time from t to ready grows and more comments expire. The replay above measures it.

### Estimate for the 29/10 PR (rule 1)

| Part | Files | Hours |
|---|---|---|
| Contracts wording, candidates and decision `card_id`, DECISIONS entry, schema tests | `contracts/`, `docs/DECISIONS.md`, `tests/` | 1.5 |
| `current_at`, used by store ordering and labeler, with tests | `app/current.py` (new), `app/store.py`, `worker/brain/labels.py` | 2 |
| Handled frames and the bounded wait, expiry to two candidates | `worker/vision/spark.py`, `worker/brain/card.py`, `worker/brain/labels.py` | 3 |
| Wait for price, draft from the next live priced version, seller pick among candidates | `orders/flow.py`, `app/orders_api.py` | 2.5 |
| JS copy of the rule, console two-candidate buttons, overlay current item | `app/static/current.js` (new), `console.js`, `overlay.html` | 1.5 |
| Four required tests, test1 0 USD replay before/after, review round | `tests/`, `measure/` | 2.5 |
| **Total** | | **≈ 13** |

Plan for two writers:

- The contracts PR goes first and is merged by the product owner.
- Writer 1 then takes `current_at`, handled frames and the wait; writer 2 takes the flow, the route and the UI.
- That is about 6–7 h of wall clock after the contracts merge, plus one review per PR.
- If the contracts PR merges by 10:00 on 29/10, both code PRs can be in review by about 17:00 on 29/10.
- Risk: the two-candidate part depends on the contracts merge. Without it, the expiry case can only go to
  `NEEDS_SELLER` with no candidates, as today.

## 2. Stock hold (after the 30/10 gate; contracts PR)

- Today the hold is computed, not stored. Orders in `CONFIRM_SENT`, `CONFIRMED` and `CLOSED` hold stock
  (`orders/flow.py:27` `HOLDS_STOCK`, `held` at `:136`), and `CANCELLED` releases it.
- A receipt only in `CONFIRMED` is today's behaviour (`orders/flow.py:641-643`, `orders/states.py:36-40`) and stays.
- New rule:
  - an order in `CONFIRM_SENT` releases its stock 10 minutes after its latest `CONFIRM_SENT`, or at the end of live,
    whichever comes first;
  - a cancel releases stock as today.
- A customer confirmation that arrives after the release does not confirm the order. Proposed: the order goes to
  `NEEDS_SELLER`, since its stock may be gone.
- Needs: a transition for the expiry, with actor, reason and a state or reason chosen in the contracts PR; the
  end-of-live event; a timer in the order flow; and tests that an expired order's stock is free for the next order
  and that a late confirmation does not confirm it. `orders/rules.py` invariants are unchanged.
- Estimate: about 4 h with tests, plus the contracts review.

## 3. Stock replies (after the gate)

- A `stock_question` gets an answer only when the item is certain (attached by rule 1, not expired) and the variant
  is certain (exactly one variant hit, as in `orders/flow.py:322-324`).
- The number is that variant's stock minus `held`, from the card's catalog data. It is never taken from speech or
  from the model.
- If either is uncertain, no answer is sent; the comment stays in the console.
- Needs: a `message_out` template (templates are free strings in `message_out.schema.json`, so no schema change) and
  the 24 h window check as for other messages.
- Estimate: about 3 h with tests.
