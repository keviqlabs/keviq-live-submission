# data/

Synthetic data only: fake catalog, labeled comments, Messenger scripts. No video and no real customer data in this repository. Phone numbers are fake (`0900 000 0xx`); user refs are pseudonyms.

- `catalog.json`: 20 fake items (sku, name, category, price in VND, variants with stock).
- `sessions/<name>/events.jsonl`: one replay session, one event per line, following `contracts/events/`. `ts_ms` values are relative to the session start; replay rebases them (`docs/DECISIONS.md` D-008). `_replay_at_ms` marks a comment that arrives later than it was written. Ids starting `fx-` are hand-written stand-ins for worker output; `python -m connectors.sim.replay --no-fixtures` leaves them out.
- `sessions/<name>/gold.json`: the expected label per comment, `referenced_card_id` for orders and `has_phone` for scams; `measure/` scores against it.
- `sessions/demo1`: about 66 s with 4 cards (one quick switch), 14 comments: late comment, "the pair just now", discount request, over-threshold order, scam with a fake phone, ambiguous "this shirt".

## comments/ (labels stream gold)

- `cards.jsonl`: 9 card events over about 9 minutes (8 cards, one retired 0.5 s before the next goes live).
- `labels.jsonl`: 200 comments (Vietnamese and English) with the gold label, `has_phone`, `has_link` and, for orders, the order fields (`sku`, `size`, `color`, `qty`, `reference_kind`, `referenced_card_id`). `arrives_ms` marks a late comment. `expected_action` (optional, orders only) is what the shop should do next, below. `connectors.sim.comments` posts the comments without the gold fields; `measure.labels` scores against them.
- `author.py` writes both files: `python data/comments/author.py`.

How the labels are set: every comment is a hand-written row in `author.py` whose code (order, price, stock, complaint, spam, scam, other) fixes its label at the moment the comment is written, and an order row also fixes its order fields. The labels exist before any model call. No model sets, suggests or edits a gold label, so the F1 of the labels stream is never scored against the model's own output. The same person who wrote these rows also wrote the labeling prompt, so the Founder reviews a sample as an outside check.

Labeling guide: `complaint` includes problems with the stream itself (lag, frozen picture, no sound), so the seller sees them.

`expected_action` on an order row (data only, not part of `contracts/`):

- `ask_customer`: the comment leaves the size or the item open; the shop asks rather than guesses. c019 "lấy đôi này" (no size), c022 "the shirt from yesterday's live" (`unresolved`), c106 "size như bạn trên" (size as the person above). The model guesses if it fills in a size where gold has none, or another `reference_kind` where gold is `unresolved`.
- `needs_seller`: the order goes to the seller: c034, 3 bags at 690,000đ (over `SELLER_THRESHOLD_VND` 2,000,000đ in `orders/rules.py`), and c092, a discount request inside an order. The model's part is to fill in every order field right.

`measure.labels` reports, per value, how many of these rows the model got right and why the others missed. Whether the ask or the NEEDS_SELLER order then happens is checked in the integration replay, not here. Other rows with no size or an unresolved item carry no `expected_action`. A cancel row is not in the set yet.

`label_source` on each row:

- `template`: the label fixed by the row's code in `author.py` (c031–c200).
- `founder_approved`: c001–c030, the Founder's review sample, confirmed by the Founder on 27 October 2026 (all 30, none changed). Reports list them apart from template gold.
- `founder_pending`: a sample label the Founder has not confirmed yet. Reports list these apart and do not count them as approved gold. None today.

## Local media (never committed)

Founder test recordings (phone video, Vietnamese and English sessions) stay on the machine under `data/local/<session>/` (gitignored, along with `*.mp4`, `*.mov`, `*.wav`, `*.m4a`). Do not use anyone else's video without written permission. A replay file built from local media references it by path (`frame.jpeg_path`), never by embedding it.
