# data/

Synthetic data only: fake catalog, labeled comments, Messenger scripts. No video and no real customer data in this repository. Phone numbers are fake (`0900 000 0xx`); user refs are pseudonyms.

- `catalog.json`: 20 fake items (sku, name, category, price in VND, variants with stock).
- `sessions/<name>/events.jsonl`: one replay session, one event per line, following `contracts/events/`. `ts_ms` values are relative to the session start; replay rebases them (`docs/DECISIONS.md` D-008). `_replay_at_ms` marks a comment that arrives later than it was written. Ids starting `fx-` are hand-written stand-ins for worker output; `python -m connectors.sim.replay --no-fixtures` leaves them out.
- `sessions/<name>/gold.json`: the expected label per comment, `referenced_card_id` for orders and `has_phone` for scams; `measure/` scores against it.
- `sessions/demo1`: about 66 s with 4 cards (one quick switch), 14 comments: late comment, "the pair just now", discount request, over-threshold order, scam with a fake phone, ambiguous "this shirt".

## Local media (never committed)

Founder test recordings (phone video, Vietnamese and English sessions) stay on the machine under `data/local/<session>/` (gitignored, along with `*.mp4`, `*.mov`, `*.wav`, `*.m4a`). Do not use anyone else's video without written permission. A replay file built from local media references it by path (`frame.jpeg_path`), never by embedding it.
