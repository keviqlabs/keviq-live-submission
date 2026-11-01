# measure/manual/ — a manual baseline on the same stream

C5 compares the assistant with what a shop does without it. This kit measures the "without" side on the same
100-comment synthetic stream the assistant was scored on (`data/comments/c5/stream-1.jsonl`, run
`rescore-paid-1031-1615`): one person watches the recorded product video and handles the comments by hand, with
no assistant, and a script scores what they did against the stream's gold.

The rules below are fixed in the commit that adds this file, before the run. A change after the run is a new
protocol and needs a new run.

## What the person sees and does

`python -m measure.manual serve` opens a local page (127.0.0.1 only):

- The product video plays once at normal speed with sound. It has no controls: it cannot be paused, sought or
  replayed. A pause, a seek attempt and the tab being hidden (another tab, or the window minimised) are logged;
  a click on another window that leaves the tab showing is not.
- Comments appear in a feed when they reach the shop: at `arrives_ms` when the stream delivers one late, else at
  `ts_ms`. The 60 s measure starts there on both sides. Each shows its author handle, its text and the minute
  and second it appeared.
- The page needs a window at least 1100 px wide for its three columns; the start screen says so when it is
  narrower.
- A catalog strip lists the ten item codes with name, price, sizes and stock.
- Clicking a comment opens four actions:
  - **Order**: pick an item code (or "unclear"), a size (or "not said") and a quantity, then create the order.
  - **Ask customer**: pick the item code if it is known and leave the size at "not said", or pick "unclear",
    then ask. The button stays off until one of them is picked.
  - **Hide**: hide the comment (final; a wrong hide stays wrong).
  - **Reply**: one click, standing for a short public reply.
- A later order or ask on the same comment replaces the earlier one.
- An action is timed at the click but shows as done (its tag, the counts, the confirmation line) only once the
  local server has stored it. If a line cannot be stored, because the server is down or refuses it, the page
  stops the run there and says so: it sends nothing more, so the log has no lock and `score` reports the run as
  incomplete, even if the server comes back before the end. If the start line itself is not stored, the run was
  never recorded (no `run.json`) and there is nothing to score.
- After the video ends there is a 30 s tail, the same tail the assistant run had (`tail_ms` 30000), then the page
  locks and refuses further actions. The whole run lasts about 6 min 22 s.

Nothing the page serves carries the gold: `/data` sends only comment id, times, author handle and text. No model
is called, no database is used and nothing leaves the machine.

## Fixed rules

1. One person, the same person through the run. No notes prepared from the stream; the person does not read
   anything under `data/comments/c5/`, the page's `/data`, the browser's developer tools, the gold or the
   assistant's results before or during the run. Before the run the person states in one line what of these
   they have already seen; the line goes into the evidence with the log.
2. One practice round first: `--practice` plays the first 60 s with a different stream (`stream-2.jsonl`). It is
   logged apart and never scored.
3. Only the first full run counts. The server refuses a second start in the same run directory; a second full
   run under another name is not prevented by the kit, and the first run's folder is the one committed. A run
   that stops early is reported as incomplete, with what it has; it is not replaced by a better one.
4. The raw action log (`actions.jsonl`), `run.json` and the score are committed under `docs/evidence/` as they
   are. Nothing is edited by hand. The run folder is copied out of `.scratch/` right after the lock, before
   anything else, because `.scratch/` is not tracked and goes when its checkout is removed.
5. The scorer, the author of this kit and the author of the C5 streams do not take part in the run.

## Scoring

`python -m measure.manual score --run NAME [--assistant FILE]` reads the log and the gold. The rows are the
ones `measure/c5_score.py` uses for the assistant:

| Measure | Rows | Rule |
|---|---|---|
| Taken up within 60 s | the 28 order comments that name an item | the first order or ask on the comment comes ≤ 60 s after it appeared; else missed (`missed()` uses the same 60 s) |
| Item right | the same 28 | the last order or ask before the lock names the gold item: right / wrong / unclear / no action (the assistant's right / wrong / no card) |
| Missing size asked | the 4 of the 28 whose gold action is to ask the customer | the last order or ask is an ask |
| Unresolved | the 2 order comments that name no item | right when the last order or ask names no item code ("unclear"); wrong when it names one; no action when there is none |
| Size and quantity right | the item-right rows | size and quantity equal the gold (human side only) |
| Phones hidden | the 4 comments with a phone number | seconds from appearing to the first hide; a phone never hidden has no time and the longest time is then "not hidden", as the assistant's score counts it without limit (`inf`) |
| Links hidden | the 6 comments with a link | as phones |
| Hide targets | the comments labelled scam or spam, or carrying a phone number or a link | how many were hidden before the lock |
| Wrong hides | every comment | hides on a comment that is not a hide target |
| Load | all actions (rate, replies); the 30 order comments (no action at the lock); the 28 (median) | actions per minute, replies, order comments with no action at the lock, median seconds from appearing to the first order or ask |
| Integrity | the run | pauses, seek attempts, playback stalls, time the tab was hidden, the largest gap between the video clock and the server clock, and actions dated before their comment appeared (not counted); a run with any of them is reported with them |

Times are on the video clock, logged by the page at each click; the server's own clock is logged beside it as a
check. Actions after the lock are not counted. A comment with several orders or asks is scored on the last one;
the first one sets the time it was taken up. A run at a speed other than 1 is refused by the scorer (the
`--speed` option exists for testing the kit); `--allow-speed` scores it and prints it as not a result.

`--assistant` takes the assistant's score JSON for the same stream and prints its numbers beside the person's:
item right / wrong / no card (the person's unclear plus no action), missed within 60 s, the number of phone rows
and the longest time a phone stayed visible. The printed table ends with an integrity line when any integrity
value is not zero; all of them are in `score.json`.

## What the result can and cannot say

It is one person on one 6-minute synthetic stream, n = 1. It can say what this person managed by hand on these
comments, beside what the assistant did on the same comments. It cannot say how many staff a shop needs, and it
does not replace the C5 baseline sentence by itself; a change to C5 goes through `docs/DECISIONS.md`.

Known biases, all in the person's favour:

- Picking an item code and a size from buttons is faster than writing an order down from a comment.
- Reply is one click instead of typing.
- The person only handles comments; a seller alone would also be presenting the products and saying prices.
- The person knows the products and their codes and has had a practice round.
- The quantity starts at 1, which is the gold quantity on 27 of the 28 rows.
- No colour is picked; no gold order names a colour.
- Order comments come from a range of author handles that other comments do not use, in both streams; a person
  can pick that up in practice. The assistant does not learn across runs.

Against the person: the comment rate is the stream's (about 17 a minute), set before this run, and every comment
must be read on screen. The assistant's replay also carried the time each late comment was written; the person
sees only when it arrived.

## Commands

```
uv run python -m measure.manual prepare --source RECORDING.mp4 --out VIDEO.mp4   # H.264 copy the browser plays
uv run python -m measure.manual serve --run NAME --video VIDEO.mp4 --practice    # practice, 60 s, not scored
uv run python -m measure.manual serve --run NAME --video VIDEO.mp4 --port 8765   # the run
uv run python -m measure.manual score --run NAME --assistant SCORE.json
```

Logs go to `.scratch/manual-baseline/NAME/` (`NAME-practice/` for practice, so a run name may not end in
`-practice`) until they are committed as evidence. A fixed `--port` lets a restarted server come back where the
page posts, but a line posted while it was down has already stopped the run.
