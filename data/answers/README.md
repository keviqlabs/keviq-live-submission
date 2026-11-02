# Answers about the item on screen (private replies, no voice)

A viewer who asks about the item being shown (its price, whether a size is in stock) gets one private reply from
the shop. Facts come only from the seller-approved card. The code picks the sentence; nothing here speaks, writes
an order, changes a price or collects money.

| file | what it does |
|---|---|
| `answers/questions.py` | what a comment asks: price, stock (with size), a policy question, or an order without a size |
| `answers/facts.py` | approved facts of a card event |
| `answers/reply.py` | `compose()` the sentence from approved facts; `check()` re-reads any sentence against a card |
| `answers/selector.py` | groups the same question about the same card for a spoken co-host (scored, not wired) |
| `answers/message.py` | `on_question()`: one `price_question` / `stock_question` label becomes at most one `message_out` |
| `answers/handlers.py` | the event-worker handler that runs `on_question()` for sessions not bound to an attachment |
| `data/answers/build_eval.py` | writes `eval-c5.jsonl`: the 5 C5 loops with synthetic card events and traps |
| `measure/answers_eval.py` | scores the selector with its own reading of each sentence and its own truth |
| `measure/answers_messages.py` | scores the private replies of `message.py` with the same judge |

`questions`, `facts`, `reply` and `selector` use the standard library and each other only.

## Rules the code keeps

- A card counts as approved when its status is `live` or `edited` and it has a price above 0. A draft gets "Giá món
  này để người bán xác nhận giúp bạn nhé." and no number.
- A stock count is said only when the approved card has a SKU and variants. Otherwise the reply is "Số lượng … để
  người bán xác nhận giúp bạn nhé." A size the card does not list gets "Size này để người bán xác nhận …". The
  wording of the hand-over is one constant, `SELLER_CONFIRMS` in `answers/reply.py`.
- Policy questions (shipping, wholesale, "is this the final price") are passed to the seller.
- The item is the card on screen when the comment was written (`app/current.py`); its facts come from that card's
  latest version. A question in the first 3 s after the item changed gets nothing (it may be about the item
  before). If another card is stored as on screen by the moment the reply is built, the reply is dropped.
- Right before the message is built, `check()` re-reads the sentence against the card's latest version found again
  in the events: same card and version, every amount equal to the approved price, every size and count equal to
  the approved stock. A mismatch drops the reply.
- One reply per comment (`event_id` `ans.<comment_id>`), sent to the comment (`recipient.comment_id`) only inside
  the 24 h window of `orders/rules.can_message`. The payload carries `comment_id`, `card_id`, `card_version`,
  `kind` and `text`; the template is `answer_question`.
- An order without a size is left to the order flow (`ask_variant`); this path does not ask for a size.

## Why the 31 October paid rescore had size 1/4 and stock 0/13

`docs/evidence/2026-10-31/rescore-paid-1031-1615-comments.jsonl`, loop 1:

- Stock 0/13: all 13 stock questions were labelled `stock_question`, but no code path replied to that label; every
  one has action `none` and no message. `measure/c5_report.py` counts a stock question as answered when a
  `message_out` carries its `comment_id`.
- Size 1/4: c5-1-021 and c5-1-048 were drafted as orders without asking for a size (the order flow sends
  `confirm_order`), and c5-1-032 had no card. These are outside this path and are not changed here.

## Eval set

`python data/answers/build_eval.py` (and `--check` to confirm the file matches). 806 rows: show (the item really on
screen), card, comment. 575 comments: the 500 C5 comments plus 75 added traps.

- Card timeline per item shown: a draft 1.5 s after the switch, the approved card at 4 s. SP03 stays a draft in loops
  1, 3 and 5. SP09 and SP10 have no SKU, so their stock is not known. In even loops the first size of SP01-SP06 goes
  to 0 at 8 s. At the second price change of SP01, SP04 and SP09 the price drops by 20 000. After every edit the
  version before it arrives again 300 ms late. Every card validates against `contracts/events/card.schema.json`.
- The gold kind of a C5 question comes from the template it was written from (`data/comments/c5/author_c5.py`),
  not from the parser under test.
- Traps per loop: a stock question 2 s before the item changes, a question 2.5 s into a new item about a size of
  the item before, and a price question 2 s before a price edit (the answer must carry the new price).

Known biases: the cards are synthetic, with a fixed 1.5 s delay between the item appearing and its card; both runs
use the set's own gold labels, so they show the answer logic, not the classifier; the selector's cooldown and
maximum wait were chosen on this same set.

## Private replies (1 November 2026, `python -m measure.answers_messages --rescore`)

Each labelled comment goes through `on_question()` over the events stored 2 s after it; the judge looks at the
real screen (`show` rows) at that moment.

| group | n | correct | answered | deferred | silent ok | wrong | no answer |
|---|---:|---:|---:|---:|---:|---:|---:|
| price | 46 | 46 | 46 | 0 | 0 | 0 | 0 |
| policy | 19 | 19 | 0 | 19 | 0 | 0 | 0 |
| stock | 65 | 65 | 45 | 20 | 0 | 0 | 0 |
| ask_size | 20 | 2 | 0 | 0 | 2 | 0 | 18 (order flow) |
| trap_price_edit_before_answer | 15 | 15 | 15 | 0 | 0 | 0 | 0 |
| trap_switch_before_answer | 40 | 0 | 0 | 0 | 0 | 40 (off screen) | 0 |
| trap_right_after_switch | 20 | 20 | 0 | 0 | 20 | 0 | 0 |
| orders and other comments | 350 | 350 | 0 | 0 | 350 | 0 | 0 |
| all | 575 | 517 | 106 | 39 | 372 | 40 | 18 |

Wrong price, stock or variant: 0. The 40 in `trap_switch_before_answer` are replies about the item the comment was
written under, built at the moment the seller moved to the next item and before that item's card exists. The facts
are right for the item they name; the judge counts them because the item is no longer on screen.

| reply built after the comment | replies | wrong price / stock / variant | about an item already off screen | dropped: item changed |
|---|---:|---:|---:|---:|
| 0 ms | 185 | 0 | 0 | 0 |
| 1000 ms | 185 | 0 | 0 | 0 |
| 2000 ms | 185 | 0 | 40 | 0 |
| 3000 ms | 185 | 0 | 40 | 0 |
| 5000 ms | 145 | 0 | 0 | 40 |

Rescore cases (loop 1, run labels). Mode a uses the cards that run had (SKU only for SP01, SP02, SP04, SP05,
SP06); mode b gives every item an approved card with stock.

| | size (4 cases) | stock (13 cases) |
|---|---|---|
| mode a | 0 asked (3 are orders for the order flow, 1 had no stock) | 6 answered, 7 "để người bán xác nhận", 0 wrong |
| mode b | 0 asked | 13 answered, 0 wrong |

Every one of the 13 stock questions gets a `message_out` with its `comment_id` in both modes.

## Selector (spoken co-host, not wired; `python -m measure.answers_eval --rescore`)

With labels: 575/575 correct, 124 answered, 39 deferred, 412 silent, 0 wrong; `trap_switch_before_answer` 40
silent. From the text alone: the same for every question group, and 15 of the 220 comments that are not questions
got a sentence. Wrong price, stock or variant: 0 in both runs.

| audio starts after | spoken | dropped by the check | passed | passed but wrong |
|---|---:|---|---:|---|
| 0 ms | 139 | 0 | 139 | 0 |
| 300 ms | 139 | 0 | 139 | 0 |
| 1000 ms | 139 | 0 | 139 | after_switch 1 |
| 3000 ms | 139 | version_changed 6 | 133 | after_switch 12 |

Rescore mode a: size 3/4 asked with the right sizes plus 1 silent, stock 6 answered and 7 deferred; mode b: size
4/4, stock 13/13.

## Not done here

- Nothing is said aloud; the selector is scored offline only.
- Which item is on screen comes from the card stream, which sees a new item about 1.5 s late. A reply built in that
  gap names the item before.
- Sessions bound to an attachment: the event worker cannot write `message_out` there, so `handlers.py` stays silent.
