# data/

Synthetic data only: fake catalog, labeled comments, Messenger scripts. No video and no real customer data in this repository. Phone numbers are fake (`0900 000 0xx`); user refs are pseudonyms.

- `catalog.json`: 20 fake items (sku, name, category, price in VND, variants with stock).
- `sessions/<name>/events.jsonl`: one replay session, one event per line, following `contracts/events/`. `ts_ms` values are relative to the session start; replay rebases them (`docs/DECISIONS.md` D-008). `_replay_at_ms` marks a comment that arrives later than it was written. Ids starting `fx-` are hand-written stand-ins for worker output; `python -m connectors.sim.replay --no-fixtures` leaves them out.
- `sessions/<name>/gold.json`: the expected label per comment, `referenced_card_id` for orders and `has_phone` for scams; `measure/` scores against it.
- `sessions/demo1`: about 66 s with 4 cards (one quick switch), 14 comments: late comment, "the pair just now", discount request, over-threshold order, scam with a fake phone, ambiguous "this shirt".

## comments/ (labels stream gold)

- `cards.jsonl`: 9 card events over about 9 minutes (8 cards, one retired 0.5 s before the next goes live).
- `labels.jsonl`: 200 comments (Vietnamese and English) with the gold label, `has_phone`, `has_link` and, for orders, the order fields (`sku`, `size`, `color`, `qty`, `reference_kind`, `referenced_card_id`). `arrives_ms` marks a late comment. `expected_action` (optional, on some order and `other` rows) is what the shop should do next, below. `connectors.sim.comments` posts the comments without the gold fields; `measure.labels` scores against them.
- `author.py` writes both files: `python data/comments/author.py`.

How the labels are set: every comment is a hand-written row in `author.py` whose code (order, price, stock, complaint, spam, scam, other) fixes its label at the moment the comment is written, and an order row also fixes its order fields. The labels exist before any model call. No model sets, suggests or edits a gold label, so the F1 of the labels stream is never scored against the model's own output. The same person who wrote these rows also wrote the labeling prompt, so the Founder reviews a sample as an outside check.

Labeling guide: `complaint` includes problems with the stream itself (lag, frozen picture, no sound), so the seller sees them.

`expected_action` (data only, not part of `contracts/`):

- `ask_customer`: the comment leaves the size or the item open; the shop asks rather than guesses. c019 "lấy đôi này" (no size), c022 "the shirt from yesterday's live" (`unresolved`), c106 "size như bạn trên" (size as the person above). The model guesses if it fills in a size where gold has none, or another `reference_kind` where gold is `unresolved`.
- `needs_seller`: the order goes to the seller: c034, 3 bags at 690,000đ (over `SELLER_THRESHOLD_VND` 2,000,000đ in `orders/rules.py`), and c092, a discount request inside an order. The model's part is to fill in every order field right.
- `confirm_cancel`, on an `other` row (contract v1 has no cancel label; decided 27 October): c192 "Shop ơi huỷ giúp em đôi size 39 nha", from customer u065, whose one earlier order is c032, size 39. The shop asks the customer to confirm in Messenger and cancels only after they do. The model's part is the label `other`.
- `none`, on an `other` row: c169 "đừng huỷ đơn em nha shop" (don't cancel my order), from u014, who ordered in c009. Nothing should be cancelled; the model's part is the label `other`.

`measure.labels` reports, per value, how many of these rows the model got right and why the others missed. Whether the ask, the NEEDS_SELLER order or the cancel then happens is checked in the integration replay, not here. Other rows with no size or an unresolved item carry no `expected_action`.

`label_source` on each row:

- `template`: the label fixed by the row's code in `author.py` (c031–c200).
- `founder_approved`: c001–c030, the Founder's review sample, confirmed by the Founder on 27 October 2026 (all 30, none changed). Reports list them apart from template gold.
- `founder_pending`: a sample label the Founder has not confirmed yet. Reports list these apart and do not count them as approved gold. None today.

## comments/c5/ (test1 played 5 times)

- `stream-{1..5}.jsonl`: 100 synthetic comments per loop of test1 (351.5 s). `comment_id` is `c5-<k>-NNN`, `ts_ms` counts from the loop start, `arrives_ms` marks a late comment. Gold per row: `label`, `has_phone`, `has_link`, and for an order `order` (`product` SP01–SP10, `size`, `color`, `qty`, `reference_kind`) plus `expected_action`. `situations` lists which C5 situations the row is meant to trigger.
- `messenger-{1..5}.jsonl`: the customer's Messenger replies for loop k: which order, after which shop message (`after`), not before `not_before_ms`, as a quick reply kind (`CONFIRM`/`CANCEL`, the runner appends `:<order_id>`) or a short text, and the order status expected after it.
- `windows.json`: segment bounds, product codes and when a fixed price was said, copied from the local test1 gold (`data/local/test1`, not committed) by `python data/comments/c5/author_c5.py windows PATH`. Nothing heard in the video is copied.
- `author_c5.py` writes the streams and scripts from `windows.json` alone, with no randomness. With the local gold present (`C5_GOLD` or the default path) it also checks the orders against its stock and prices. `tests/test_c5_streams.py` checks the minimum counts, the phone pattern, the windows and that the files match the generator.

Timing: a comment about the item on screen sits from its window start + 8 s to its end − 5 s; a "vừa nãy" comment sits within 60 s after the switch, at least 8 s in. Labels are fixed by code, as in `author.py` (`label_source: template`). New `expected_action` values here: `confirm_order` (the customer confirms in Messenger), and on `other` rows `confirm_cancel` and `none`, as above.

## Local media (never committed)

Founder test recordings (phone video, Vietnamese and English sessions) stay on the machine under `data/local/<session>/` (gitignored, along with `*.mp4`, `*.mov`, `*.wav`, `*.m4a`). Do not use anyone else's video without written permission. A replay file built from local media references it by path (`frame.jpeg_path`), never by embedding it.
