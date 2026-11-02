# Manual baseline on C5 stream-1: run `founder-1`, 1 November 2026

One person handled stream-1 of the C5 comment set by hand at normal speed, with the manual-baseline kit of pull
request #79 (head `1e37fb64d0184eba2ab9a1c60e4a67d34b737f55`; `run.json` records `git_dirty: false` and speed 1.0).
The run started at 08:00:18 ICT, the video ended at 08:06:10 and the page locked at 08:06:40 after the 30 s tail.
Stream `data/comments/c5/stream-1.jsonl` (sha256 `9c259c03…9e79c`, 100 rows) and catalog
`data/comments/c5/catalog.json` (sha256 `fd5e86ad…7adc73`) are the ones the assistant session below used. The person
watched `test1-h264.mp4` (26225951 bytes, sha256 `02cd6975…f176fc`, 351.5 s, not committed), the kit's H.264 copy of
the test1 recording `vi-2026-10-27.mp4` (not committed). The assistant session got a recording as 350 frames and one
transcript played by `worker.media` (`../2026-10-31/rescore-paid-1031-1615-loop1-media.log`); its files do not name
the recording or give its hash.

`manual-founder-1/` holds the kit's raw `actions.jsonl` and `run.json` as the kit wrote them, the scorer's
`score.json` and its printed table `score.txt`. `manual-founder-1-practice/` holds the three practice logs. No file
was edited by hand.

The assistant column is session `rescore-paid-1031-1615`, run on main `bfc7611` on 31 October with the same stream
and catalog: `../2026-10-31/rescore-paid-1031-1615.json` (sha256 `05fa3d4e…f59d0041`), which pull request #78 adds,
and the section "Stage 2 again, on main: rescore-paid-1031-1615" that #78 adds to
`../2026-10-31/rescore-paid-1031-NOTE.md`. That note is on main; the JSON, the session's logs (the media log above
among them) and the section are not yet.

The team states that the person is a member of the team who knows the product and its catalogue, ran the run alone,
did not write the kit, the C5 streams or the scorer, and did not score the run. All commits in this repository are
made from one GitHub account, so commit authorship does not show who wrote a file. No model was called for this run;
its cost is 0 USD.

## Result (verbatim, `score.txt`)

```
status complete, 6.36 min
                                                   person  assistant
order comments that name an item                       28         28
item right                                              2         15
item wrong                                              3          0
no item named (unclear + no action / no card)          23         13
  of which no action                                   23          -
not taken up within 60 s                               23         13
phones hidden                                         2/4     4 rows
longest a phone stayed visible, s              2 not hidden      0.042
links hidden                                          1/6          -
wrong hides                                             0          -
integrity: pauses 0, seeks 0, stalls 0, tab hidden 25.5 s, clock drift 0.1 s, early actions 0
written docs/evidence/2026-11-01/manual-founder-1/score.json
```

Of the 28 order comments that name an item, the person took up 5 within 60 s and named the right item on 2 and a
wrong item on 3; 23 had no order at the lock. The assistant session's label of the comment referred to a card of the
gold item on 15 (current card 13, previous card 2): it made an order on 14 and, on `c5-1-010`, whose gold action is
to ask the customer, asked which variant, so its 15 include one question, while the person's 2 are both orders. Its
label referred to another item's card on 0 and to no card on 13, where it made no order and asked nothing. The
scorer gives a card the item whose span of the video holds the frame that opened it. Those 13 are also the 13 it did
not take up within 60 s. For 10 of them the gold item never had a card with an item code in that run, which the
attachment contract (`contracts/comment_attachment.md`) requires before an order exists; the other 3 are not
explained by its files (pull request #78's note). Three of the person's five orders (`c5-1-074`, `c5-1-078`,
`c5-1-091`) are on comments among those 13. In the table, the assistant's "4 rows" is the number of phone comments;
the #78 note gives 4 of 4 hidden. The person hid 2 of the 4 phone comments (after 8.689 s and 6.12 s) and 1 of the 6
link comments (after 8.288 s), and hid nothing that should have stayed. This is one person, one run and one stream.

From `score.json`:

- 18 actions in 6.36 min (2.83 a minute): 6 hides, 7 replies, 5 orders, 0 asks. All 5 orders have quantity 1, the
  gold quantity on all 5, and none names a size. The scorer checks size and quantity only on the 2 item-right
  orders: quantity is right on both, and size on 1 (`c5-1-091`, whose gold names no size; on `c5-1-024` the gold
  size is 39).
- Median 14.318 s from a comment appearing to its order, over the 5 order comments that have one.
- Missing size asked: 0 of 4 (the #78 note gives 1 of 4 for the assistant's ask-for-size situation, from its own
  scorer, not this one). Unresolved comments (`c5-1-001`, `c5-1-036`): no action on either.
- Order backlog at the lock: 25 comments, the 23 untaken order comments and the 2 unresolved ones.
- One reply (`c5-1-031`) went to an order comment (gold SP02 size 40, from the previous card). The score counts an
  order only from an order or ask action, so this row is in "no action".
- One hide (`c5-1-041`) was an order comment that also carries a phone number (gold SP04 size L). It counts as a
  phone hidden and as an order comment with no action.
- The other three hides (`c5-1-050`, `c5-1-090`, `c5-1-093`) are spam or scam comments without a phone or link;
  they are among the 26 hide targets, and `wrong_hides` is empty.

## The order rows

The item on screen comes from `data/comments/c5/windows.json`, at the time the comment appeared in the feed and at
the click. Times are video milliseconds from `actions.jsonl` and the stream.

| Comment | Gold | Appeared | On screen then | Click | On screen at click | Code picked | Item |
|---|---|---|---|---|---|---|---|
| `c5-1-021` | SP02, ask the customer (no size) | 80100 | SP02 | 94418 | SP03 | SP01 | wrong |
| `c5-1-024` | SP02 size 39 | 97900 (written at 85850, delivered late by the stream) | SP03 | 106618 | SP03 | SP02 | right |
| `c5-1-074` | SP07 | 240300 | SP07 | 259542 | SP08 | SP10 | wrong |
| `c5-1-078` | SP08 | 258600 | SP08 | 274497 | SP09 | SP09 | wrong |
| `c5-1-091` | SP10 | 320050 | SP10 | 332784 | SP01 | SP10 | right |

In each of the three wrong rows the video had moved to the next item by the time of the click; in one of them
(`c5-1-078`) the code picked is the item on screen at the click. `c5-1-018` (gold SP01, from the previous card,
appeared at 76850) was never acted on. If the SP01 order placed on `c5-1-021` was meant for `c5-1-018`, item right
and wrong would read 3 and 2. That is a reading of the log, not a measurement; the score stays as the scorer wrote
it.

## Integrity

- No pause, seek or stall, and no action before its comment appeared.
- The tab was hidden once, at video 356003 ms (08:06:14 ICT), 4.5 s after the video ended at 351533 ms, and stayed
  hidden until the lock at 381533 ms: 25.53 s. The last comment appeared at 342927 ms, so none appeared while the
  tab was hidden. The 60 s windows of three order comments ran into that time: `c5-1-091` (already taken at 332784),
  `c5-1-095` (appeared 335825) and `c5-1-098` (339875); the last two were not taken.
- Clock drift, the server's elapsed time against the video position: 65 to 71 ms on the 18 clicks and 85 to 88 ms
  on the end, hidden and lock events; the largest is 0.088 s.

## Departures from the kit's rules

The kit's fixed rules are in `measure/manual/README.md` (pull request #79). This run departs from rules 1, 2 and 4.
Items 1 and 2 and the kit author's part under rule 5 rest on the team's statement: no committed file records the
request for the line, the team's chat or the 08:36 confirmation. The paragraphs on rules 3 and 5 say what the files
show and what the team states.

1. Rule 1 asks the person to state in one line, before the run, what they have already seen. The line was asked for
   before the run, but the person started the run without giving it. The line below was written after the run from
   what the person had been shown, and the person confirmed it at 08:36 ICT, about 30 minutes after the lock:

   > Before the run, the person saw the assistant's aggregate numbers on this stream (15/28 right, 0 wrong, phones
   > hidden in under 0.1 s), but no individual comments, labels or answers, and had not watched stream-1 before.

2. Rule 1 also says the person does not read the assistant's results before or during the run. The aggregate numbers
   in that line were shown to the person in the team's chat before the run, by the kit author, so the run breaks
   that part of rule 1. The README's list of biases does not name it, and this note does not estimate which way it
   moves the person's result.
3. Rule 2 asks for one practice round. The person played three, each the first 60 s of `stream-2.jsonl` on the same
   kit head with a clean tree, logged under `manual-founder-1-practice/` and not scored:
   - `founder-1-practice`, 31 October 21:59 ICT: no action; the tab was hidden at 22.5 s.
   - `founder-1b-practice`, 31 October 22:32 ICT: one reply.
   - `founder-1c-practice`, 1 November 07:55 ICT: one reply and one order.
4. Rule 4 asks that the run folder be copied out of `.scratch/` right after the lock, before anything else. At the
   lock a script copied `actions.jsonl` and `run.json` from the kit checkout's `.scratch/` into a `.scratch/` folder
   of the repository's main checkout, which is also untracked. The scorer then ran on the kit checkout's folder at
   08:06:55 ICT, and the files were put under `docs/evidence/` at 08:39 ICT. The committed `actions.jsonl`, `run.json`
   and `score.json` have the same sha256 as the copies still in both `.scratch/` folders.

Rule 3: the only stream-1 run at speed 1 in the kit's `.scratch/manual-baseline/` is `founder-1`. The team states it
is the person's only full run and that the person did not watch the kit author's checks: on stream-1 on 31 October
between 20:11 and 20:38 ICT at speeds 2, 8 and 16, and on the stream-2 practice segment, all on a tree with
uncommitted changes. Those logs are not committed. To check the kit, its author also ran the scorer on three of them
(`kitcheck-speed8`, `kitcheck-actions` and `kitcheck-practice`, at speeds 8, 2 and 4, between 20:13 and 20:19 ICT);
none of those scores is used here.

Rule 5 says the scorer, the kit author and the author of the C5 streams do not take part in the run. The person is
none of them, by the team's statement above. The team states that the kit author served the page, guided the
practice rounds in the team's chat, showed the aggregate numbers (item 2), started the scorer and sent the person no
message between the start and the lock. The scorer's `score.json` in the kit's `.scratch/` is dated 08:06:55 ICT,
15 s after the lock.

## Caveats

- n = 1. The kit README lists the biases of the setup. In the person's favour: codes and sizes picked from buttons,
  a reply in one click, only the comments to handle, knowing the products and a practice round (three here), a
  quantity that starts at 1 (the gold quantity on 27 of the 28 rows), no colour to pick, and order comments coming
  from author handles that other comments do not use, which practice can teach. Against the person: about 17
  comments a minute, each read on screen, with only the time a late comment arrived shown.
- The README's scoring table names "the 28" as the population for the median; only the 5 rows with an order have a
  time, so the median is over those 5.
- The assistant column is the kit scorer's reading of `rescore-paid-1031-1615.json`. It was not recomputed from the
  session's events for this note, and neither was the 0.042 s phone figure.
- `score.txt` is the output of a second run of the scorer on the same files. It matches the first run except for its
  last line, which in the first run named `score.json` by a local absolute path. The second run rewrote
  `score.json` with the same bytes.
- This run cannot say how many staff a shop needs, and it does not by itself replace the baseline sentence for C5;
  a change to it goes through `docs/DECISIONS.md`.

## Reproduce

Once pull requests #79 (the kit) and #78 (the assistant's JSON) are on main:

```
uv run python -m measure.manual score --dir docs/evidence/2026-11-01/manual-founder-1 --assistant docs/evidence/2026-10-31/rescore-paid-1031-1615.json
```

The scorer rewrites `score.json` in place; `git diff` should then show no change, and the printed table should match
`score.txt`.

## Files (sha256)

| File | sha256 |
|---|---|
| `manual-founder-1/actions.jsonl` | `6bf671aea78811bd95eba96c2e1f1edcf2b3250376ebde5768fd432c2e686723` |
| `manual-founder-1/run.json` | `525e0dc59914fa1a72585af0162449a116f8c30862bba3da8faadd170f06b37f` |
| `manual-founder-1/score.json` | `972a763846d95357aff600d66d7888d0478a3eb03c4bce1af765198dba7a9b4c` |
| `manual-founder-1/score.txt` | `17f93d3d24c60d3789f22fa401aaa45f5f8db4f52f7e1b879b2875628d5acaa7` |
| `manual-founder-1-practice/founder-1-practice/actions.jsonl` | `d68a4920045b1bd532ed0e49231a3b222496e3a960f82f565740d6ae2a9f92c1` |
| `manual-founder-1-practice/founder-1b-practice/actions.jsonl` | `ca99817632f673ad32c70475139b5e9c46c0cc3640253e4089580499b763d520` |
| `manual-founder-1-practice/founder-1c-practice/actions.jsonl` | `4811b54ae3d61d40c08e7e29fe65dbc7de8f052eda1329fd2cb2d2b49eb4533f` |
| `manual-founder-1-practice/founder-1-practice/run.json` | `5275f9c4fe074a98b7a5910141228d7c978709f7443010a998b2551e665036c5` |
| `manual-founder-1-practice/founder-1b-practice/run.json` | `cb9c600cf4f027551934519dccebebc9cc82c01226f65351484ace6508328d9f` |
| `manual-founder-1-practice/founder-1c-practice/run.json` | `ecc3e1993bc8ff51c5fc62d2a8f67cf7d24d521f29c1c6953cbb7d47238379de` |
