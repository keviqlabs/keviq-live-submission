# data/comments/c5/ (test1 played 5 times)

**Simulated data.** Every comment and Messenger reply here is written by `author_c5.py` from templates in its own code. No real person's name: `user_ref` is a pseudonym (`k<loop>-u<NN>`). No real phone number: every number, in digits or spelled out in words, is a fake one of the form `0900 000 0xx`. Nothing is taken from the Page: no real comment, no Page name, no real product name; items are named only by their code (SP01–SP10) or by a generic word ("đôi", "cái", "món").

## Files

- `stream-{1..5}.jsonl`: 100 comments per loop of test1 (351.5 s). `comment_id` is `c5-<k>-NNN`, `ts_ms` counts from the loop start, `arrives_ms` marks a late comment. Gold per row: `label`, `has_phone`, `has_link`, and for an order `order` (`product`, `size`, `color`, `qty`, `reference_kind`) plus `expected_action`. `situations` lists which C5 situations the row is meant to trigger.
- `messenger-{1..5}.jsonl`: the customer's Messenger replies for loop k: which order, after which shop message (`after`), not before `not_before_ms`, as a quick reply kind (`CONFIRM`/`CANCEL`, the runner appends `:<order_id>`) or a short text, and the order status expected after it.
- `windows.json`: segment bounds, product codes and when a fixed price was said, in ms. Copied from the local test1 gold (`data/local/test1`, not committed) by `python data/comments/c5/author_c5.py windows PATH`. Nothing heard in the video is copied.

`python data/comments/c5/author_c5.py` rewrites the streams and scripts from `windows.json` alone, with no randomness. With the local gold present (`C5_GOLD` or the default path) it also checks the orders against its stock and prices. `tests/test_c5_streams.py` checks the counts, the phone pattern (digits and words), the windows, and that the files match the generator.

## Gold

Labels are fixed by code before any model call, as in `../author.py` (`label_source: template`).

Timing: a comment about the item on screen sits from its window start + 8 s to its end − 5 s. A "vừa nãy" comment sits within 60 s after the switch, at least 8 s in.

`expected_action` values added here (data only, not part of `contracts/`):
- `confirm_order`: the customer confirms in Messenger.
- `confirm_cancel` and `none`, on `other` rows, as in `data/README.md`.
