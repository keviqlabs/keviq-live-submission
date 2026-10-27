# data/

Synthetic data only: fake catalog, labeled comments, Messenger scripts. No video and no real customer data in this repository. Phone numbers are fake (`0900 000 0xx`); user refs are pseudonyms.

- `catalog.json`: 20 fake items (sku, name, category, price in VND, variants with stock).
- `sessions/<name>/events.jsonl`: one replay session, one event per line, following `contracts/events/`. `ts_ms` values are relative to the session start; replay rebases them (`docs/DECISIONS.md` D-008). `_replay_at_ms` marks a comment that arrives later than it was written. Ids starting `fx-` are hand-written stand-ins for worker output; `python -m connectors.sim.replay --no-fixtures` leaves them out.
- `sessions/<name>/gold.json`: the expected label per comment, `referenced_card_id` for orders and `has_phone` for scams; `measure/` scores against it.
- `sessions/demo1`: about 66 s with 4 cards (one quick switch), 14 comments: late comment, "the pair just now", discount request, over-threshold order, scam with a fake phone, ambiguous "this shirt".

## comments/ (labels stream gold)

- `cards.jsonl`: 9 card events over about 9 minutes (8 cards, one retired 0.5 s before the next goes live).
- `labels.jsonl`: 200 comments (Vietnamese and English) with the gold label, `has_phone`, `has_link` and, for orders, the order fields (`sku`, `size`, `color`, `qty`, `reference_kind`, `referenced_card_id`). `arrives_ms` marks a late comment. `connectors.sim.comments` posts the comments without the gold fields; `measure.labels` scores against them.
- `author.py` writes both files: `python data/comments/author.py`.

How the labels are set: every comment is a hand-written row in `author.py` whose code (order, price, stock, complaint, spam, scam, other) fixes its label at the moment the comment is written, and an order row also fixes its order fields. The labels exist before any model call. No model sets, suggests or edits a gold label, so the F1 of the labels stream is never scored against the model's own output. The same person who wrote these rows also wrote the labeling prompt, so the Founder reviews a sample as an outside check.

`label_source` on each row:

- `template`: the label fixed by the row's code in `author.py` (c031–c200).
- `founder_pending`: c001–c030, the Founder's review sample, not yet confirmed. Reports list these apart and do not count them as approved gold.
- `founder_approved`: a sample label the Founder has confirmed. After the review, set `FOUNDER_STATUS` in `author.py` (and fix any label the Founder changed), then re-run the script.

## Local media (never committed)

Founder test recordings (phone video, Vietnamese and English sessions) stay on the machine under `data/local/<session>/` (gitignored, along with `*.mp4`, `*.mov`, `*.wav`, `*.m4a`). Do not use anyone else's video without written permission. A replay file built from local media references it by path (`frame.jpeg_path`), never by embedding it.
