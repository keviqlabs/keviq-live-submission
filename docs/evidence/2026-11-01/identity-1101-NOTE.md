# Product identity: the 13 order comments without a card (1 November)

Question: how many of the 13 order comments that got no card in the paid rescore run on main
(`rescore-paid-1031-1615`, root README line 14: 15/28 right, 0 on a wrong card, 13 with no card) can the card brain
rescue without ever naming a wrong item, and without losing any of the 15 that are right?

Answer at the level measured (0 USD, offline, brain level, no paid run):

- **1 rescued** (c5-1-031), **12 still blocked**, each with its blocker below. **0 on a wrong item** in every view.
- The 14 right comments that the proxy observations can show stay right on the new tree. The 15th (c5-1-041) is not
  right on either tree, for a reason that is in the observations, not the code (see "c5-1-041").
- Not measured: a paid run with live vision. A live rescue of c5-1-031 still needs its attachment to become ready.

## Trees tested

| | commit | tree |
|---|---|---|
| before | `635d8f4674597f8f1e94f5b68039d55aa09e5d00` (main at the start of this work) | `ca478dfc60fab7b5daa4e2c037826590b050b7ec` |
| after | `269018cac232d91187922b94c16d60e2ca283711` (fix/product-identity: code + tests) | `3b0718b630bd4c598accbd5bb41b5c6d79dace01` |
| first after (replaced) | `b3c263d42b90ffc6f11d3865c00d8c045740cb7d` (the code of 8cef645) | `6a756d96e0c29be2af5f6a043e2b5891fc121f37` |

Each tree is printed by the replay wrapper (`git: <commit> tree <tree> status-lines 0`, a clean worktree), see
`identity-before-1101-1900-wrapper.log` and `identity-after-1101-2008-wrapper.log`. The first after tree emitted no
event for a repeated reading (see "C1" below); its replay `identity-after-1101-1909-*` is kept only in git history
(`git show 8cef645:docs/evidence/2026-11-01/identity-after-1101-1909-events.jsonl`) and is used here only for the
C1 row.

## The change

One rule in `worker/brain/card.py` (`on_product_seen`): when vision reports the same category and colour again
while the current card has no SKU and is not retired, that is the same item. No new card, no retired card. The kept
card gets one new version that only moves `provenance.frame_ts_ms` to the repeated frame, with the repeated
product_seen first in `caused_by`, so every product_seen still has a consequence within the cycle (D-009,
`contracts/acceptance.md:9`). The version uses the existing `card` event and fields; SKU, name, price, status,
variants, stock and the current/previous item stay as they are.

Why it matters: parallel frame reads report one item several times as new. Before, each repeated report of an item
the catalog cannot name opened a new SKU-less draft and retired the one before. In the sandal segment of these
observations that made three "Sandal trắng" cards within 1.3 s (93.6 s, 93.9 s, 94.9 s from the first comment),
so "the previous item" pointed at a retired duplicate of the sandal instead of the white sneaker shown before it.
After the change there is one sandal card and the previous card is the SP02 card.

| tree | product_seen | cards | cards without SKU |
|---|---|---|---|
| before | 45 | 31 | 26 |
| after | 45 | 18 | 13 |

The first after tree had the same 18 / 13. The after run has 77 card versions: the 64 of the first after tree plus
one frame-only version for each of the 13 repeats. 635d8f4 already writes the same kind of version when an
identified item is read again (10 frame-only versions in the before run, 90 card versions in all).

Tests in `tests/test_identity_continuity.py`:

- `test_repeated_reading_of_the_current_unidentified_item_keeps_one_card`: red on 635d8f4 (`assert 3 == 1`, three
  cards for one item), green after.
- `test_each_repeated_reading_is_a_version_of_the_kept_card`: each repeat gives exactly one version of the kept card,
  caused by the repeat, that moves only the frame, and `measure.core.c1` has no missing trigger. Red on the card
  brain of 8cef645 (no event for the repeat), green after.
- `test_a_second_reading_of_a_priced_item_without_sku_sets_nothing_and_confirms_nothing`: a seller-priced (550000)
  'other trắng' card without SKU is current, the same item is read again, then an order comment for the current
  item. The repeat stays on the same card, no version sets a SKU or changes the price, the order ends NEEDS_SELLER
  and no confirmation goes out. Red when the guard is changed to fire only for draft cards (the repeat then opens a
  second card), green after.

## C1 (D-009)

`measure.core.c1` over the committed replay events (5000 ms cycle; triggers are every product_seen and every
actionable comment_labeled), and, for product_seen only, whether any event names it in `caused_by` at any time:

| tree | C1 in cycle | product_seen with a consequence (any time) |
|---|---|---|
| before (635d8f4) | 70/109 | 41/45 |
| first after (b3c263d) | 57/109 | 28/45 |
| after (269018c) | 70/109 | 41/45 |

On the first after tree the 13 repeats that kept one card (ps-1-07, 08, 20, 21, 25, 26, 31, 35, 36, 39, 40, 41,
43) produced no event. On 269018c each of them has a version of the kept card caused by it, 8 to 42 ms after the
repeat was ingested. The same 4 product_seen have no consequence on every tree (ps-1-10, 24, 27, 33): each arrived
after a later frame of the same item had been taken, and the card brain ignores an older frame
(`worker/brain/card.py:213`), as on 635d8f4; that is not changed here.

What did not change: a repeated reading never sets a SKU. A card gets a SKU only as before (label + colour giving
exactly one catalog item, refused when it disagrees with the stored observation of that item). Seller routing,
contracts, event schemas and order rules are untouched.

## Method

1. **Same input on both trees.** The paid run 1615 cannot be replayed: its events are not in any database this work
   could read. The paid run 0742 (same stream, same day, run on `4c53e72`, a branch head; its record has 14 right,
   0 wrong, 14 no card) is in the paid test database, and its stored vision observations were read with SELECT only.
   It is the closest input available, and it is used as a proxy for 1615. Each tree replayed those observations
   through the full stack (`measure.c5 --observations`, 1 loop, 0 model calls, 0 USD) on its own fresh database.
   Every run (before, after, and the first after): runner rc 0, `SETTLED/SETTLED behind 0, retired 12,
   waits {'many': 2}, failures 0`, 0 model_call events.
2. **Why an offline attachment step.** A frozen replay has no worker frame reads, so every comment attachment
   expires at its 4 s deadline (known limit, `docs/evidence/2026-10-29/rescore-bind-1029-2218-NOTE.md:109-130`).
   The replay records therefore show almost no cards on either tree (raw score below). The calculator in this note
   answers the question the paid run answers: which card would each order comment carry if its attachment became
   ready. It uses the app's own `app.current.current_at` over the card versions ingested by the snapshot time, and
   the label's `reference_kind`, as `app/attachments.py` resolves a reference.
3. **Calculator checked against two paid records** (attachment snapshot time as stored):
   - `rescore-paid-1031-0742`: 14 right / 14 no card, the same case for case as that record.
   - `rescore-paid-1030-2248`: 12 right / 16 no card against the record's 10 / 18. The two differences (c5-1-006,
     c5-1-056) are both attachments that expired in that run; on every ready attachment the calculator matches.
   - Neither record has a wrong card, so the calculator's "wrong item" path was not exercised by a paid record.
4. Views: at comment + 4 s (the attachment deadline) and at the time the label was stored. Both views give the same
   verdicts on both trees. Using the paid record's labels instead of the replay's gives the same verdicts.

## The 13 comments (gold item from the 1615 record; "at deadline" view)

| comment | gold | refers to | before (635d8f4) | after (269018c) | status |
|---|---|---|---|---|---|
| c5-1-029 | SP03 | current | no card: current "Sandal trắng", no SKU, 280000 live | no card: same card | blocked, G1 |
| c5-1-031 | SP02 | previous | no card: previous is a retired duplicate sandal card | **SP02**: previous is "SP02 giày trắng" 320000 live | **rescued** |
| c5-1-032 | SP03 | current | no card: as c5-1-029 | no card: same | blocked, G1 |
| c5-1-035 | SP03 | current | no card: as c5-1-029 | no card: same | blocked, G1 |
| c5-1-045 | SP03 | previous | no card: previous is the SKU-less sandal card | no card: same | blocked, G1 |
| c5-1-071 | SP07 | current | no card: "other đen", no SKU, 450000 live | no card: same | blocked, G1 |
| c5-1-074 | SP07 | current | no card: as c5-1-071 | no card: same | blocked, G1 |
| c5-1-078 | SP08 | current | no card: "other trắng", no SKU, 550000 live | no card: same | blocked, G1 |
| c5-1-082 | SP09 | current | no card: unpriced "other xanh" draft | no card: same | blocked, G3 then G1 |
| c5-1-085 | SP09 | current | no card: unpriced "other xanh" draft | no card: same | blocked, G3 then G1 |
| c5-1-091 | SP10 | current | no card: a stray "other đỏ" reading opened an unpriced current card | no card: same | blocked, G3 then G1 |
| c5-1-095 | SP01 | current | no card: "Giày sneaker đen" draft, no SKU | no card: same | blocked, G2 |
| c5-1-098 | SP01 | current | no card: as c5-1-095 | no card: same | blocked, G2 |

No row is a wrong item on either tree. Every "no card" above is a comment whose order waits for the seller to pick
the item, which is the waiting state the product needs when the item cannot be determined.

## Blockers (not fixed here; each one changes how order work reaches the seller)

- **G1: the item is never identified from the picture, even after the seller prices it.** SP03 (4 comments): the
  frames show an off-white slide, vision reads "sandal trắng" while the seller says "Đôi màu xám". Sandal + white
  points to SP02, which disagrees with the stored SP02 observation (white sneaker), so the brain correctly refuses
  the SKU; a vision prompt change cannot fix what the frame shows. SP07/SP08 (3 comments): the catalog has no
  category for controllers and keyboards, vision says "other", and "other" + black fits SP07 and SP09, "other" +
  white fits SP08 and SP10. Proposal: identify the item at the line that gives its first price, only when the
  vision category, the kind the seller names, the colour (the seller's, else vision's) and the spoken price fit
  exactly one catalog item; otherwise unchanged. Narrower alternative: use the one colour the seller says with the
  vision label (covers SP03 only). Either needs a decision on the attachment contract first.
- **G2: a known item shown again after an unidentified card.** SP01 returns at the end; the card before it has no
  SKU, so the rule that keeps a one-frame category jitter from restoring an old priced item refuses to reopen SP01.
  Proposal: reopen the known card when the reading agrees with its stored category and colour and the unidentified
  card in between was current for at least 3 s or got a price; a shorter interval stays a jitter.
- **G3: vision colour flips and stray readings.** SP09: vision alternates black and blue on the black keyboard, so
  the price line has several candidate cards and waits. SP10: one "other đỏ" reading at 316 s made a new unpriced
  current card just before the comment. Both are also "other", so they need G1 as well.

## Regression check on the right comments

Right at the deadline view, before: 006 010 016 018 021 024 043 047 048 054 055 056 060 062 (14).
After: the same 14 plus 031 (15). Wrong item: 0 before, 0 after, in every view.

### c5-1-041

Right in the 1615 record, no card on both trees here. In the 0742 observations the SP04 shirt frame is at 129.5 s,
before the comment at 132.7 s. The SP04 card (and the product_seen it comes from) was ingested at 138.0 s, 8.5 s
after its frame and past the comment's attachment deadline at 136.7 s, so the card versions available at the
deadline do not contain it and the current card at the comment is still the sandal card. The timing is the same on
both trees (frame 129.5 s, ingested 138.0 s), so the 'no card' comes from that delay in the 0742 observations, not
from a change of this branch. The 1615 record has c5-1-041 right, so its SP04 card was available in time; its event
timing is not in any file here.

## Raw replay records (attachments expired)

`identity-before-1101-1900-comments.jsonl` and `identity-after-1101-2008-comments.jsonl`, scored the same way:
28 order comments, 5 right / 2 on another card / 21 no card, identical on both trees. The 2 (c5-1-018 gold SP01,
c5-1-055 gold SP04) are "previous item" comments whose attachment expired; the order stops at NEEDS_SELLER
(waiting for the shop, no confirmation sent) and the record carries the current card (SP02, SP05). This is not
caused by this branch (identical on 635d8f4). Whether a live run can show it when a previous-item attachment
expires was not checked here; it sits in `orders/` and `app/`, outside this work's paths.

## Files

- `identity-before-1101-1900-*`, `identity-after-1101-2008-*`: replay wrapper logs and comment records.
- `identity-before-1101-1900-events.jsonl`, `identity-after-1101-2008-events.jsonl`: the replay events of types
  card, comment, comment_labeled, comment_attachment and product_seen (calculator input).
- `identity-1101-paid-0742-events.jsonl`, `identity-1101-paid-2248-events.jsonl`: the same event types read with
  SELECT from the paid test database for sessions `rescore-paid-1031-0742` and `rescore-paid-1030-2248`.
- In all four, `image_url` is set to null and `readiness_evidence` is dropped (frame images; the calculator does not
  read them).
- `identity-1101-attach.txt`: the calculator output for every view above, produced from the block below.

Reproduce from the repository root:

```
sed -n '/^```python$/,/^```$/p' docs/evidence/2026-11-01/identity-1101-NOTE.md | sed '1d;$d' > /tmp/attach.py
uv run python /tmp/attach.py docs/evidence/2026-11-01/identity-1101-paid-0742-events.jsonl --at actual
uv run python /tmp/attach.py docs/evidence/2026-11-01/identity-after-1101-2008-events.jsonl --at deadline
```

C1 row (any events file above):

```
uv run python -c "
import json, sys
sys.path.insert(0, '.')
from measure.core import c1
ev = sorted(({**r['body'], 'seq': r['seq'], 'ingested_ms': r['ingested_ms'], 'type': r['type'], 'ts_ms': r['ts_ms']}
             for r in map(json.loads, open(sys.argv[1]))), key=lambda r: r['seq'])
r = c1(ev); used = {c for e in ev for c in e.get('caused_by') or []}
ps = [e['event_id'] for e in ev if e['type'] == 'product_seen']
print('c1', r['covered'], '/', r['n'], 'product_seen any time', sum(p in used for p in ps), '/', len(ps))
" docs/evidence/2026-11-01/identity-after-1101-2008-events.jsonl
```

```python
"""Offline attachment: the card each order comment would carry if its attachment had become ready.
Uses the app's own current_at (media-time current/previous) over the card versions ingested by the snapshot
time, and the stored label's reference_kind, as app/attachments.py resolve_reference does.
usage: attach.py <events.jsonl> [--labels <events.jsonl>] [--at deadline|label|actual]"""
import json, sys
sys.path.insert(0, ".")  # run from the repository root
from app.current import current_at

def rows(path):
    out = []
    for line in open(path):
        r = json.loads(line)
        if "body" in r:
            r = {**r["body"], "seq": r["seq"], "ingested_ms": r["ingested_ms"], "type": r["type"], "ts_ms": r["ts_ms"]}
        out.append(r)
    return sorted(out, key=lambda r: r["seq"])

args = sys.argv[1:]
ev = rows(args[0])
lab_src = rows(args[args.index("--labels") + 1]) if "--labels" in args else ev
mode = args[args.index("--at") + 1] if "--at" in args else "deadline"
GOLD = {}
for line in open("docs/evidence/2026-10-31/rescore-paid-1031-1615-comments.jsonl"):
    r = json.loads(line)
    if r.get("gold_item"):
        GOLD[r["comment_id"]] = r["gold_item"]
comments = {r["comment_id"] if "comment_id" in r else r["event_id"].split(".")[-1]: r for r in ev if r["type"] == "comment"}
labels = {}
for r in lab_src:
    if r["type"] == "comment_labeled":
        labels[r["comment_id"]] = r  # the latest label per comment
actual = {r["comment_id"]: r for r in ev if r["type"] == "comment_attachment"}
cards_all = [r for r in ev if r["type"] == "card"]
out = {}
for cid, gold in GOLD.items():
    c = comments.get(cid)
    lab = labels.get(cid)
    if c is None:
        out[cid] = ("missing comment", None, None); continue
    kind = ((lab or {}).get("order_draft") or {}).get("reference_kind")
    if mode == "deadline":
        at = c["ts_ms"] + 4000
    elif mode == "label":
        at = max(c["ts_ms"], (lab or {}).get("ingested_ms", c["ts_ms"]))
    else:
        a = actual.get(cid)
        at = a["snapshot_at_ms"] if a and a.get("snapshot_at_ms") else c["ts_ms"] + 4000
    cards = [r for r in cards_all if r["ingested_ms"] <= at]
    cur, prev = current_at(cards, c["ts_ms"])
    if kind == "current_card":
        target = cur
    elif kind == "previous_card":
        target = prev if cur is not None and prev is not None else None
    elif kind == "explicit_sku":
        sku = (lab.get("order_draft") or {}).get("sku")
        m = [x for x in (cur, prev) if x and x["sku"] == sku]
        target = m[0] if len(m) == 1 else None
    else:
        target = None
    brief = lambda x: x and f"{x['card_id'][-6:]}:{x['sku']}:{x['price']}:{x['status']}"
    item = target["sku"] if target else None
    v = "none" if not item else ("right" if item == gold else "WRONG")
    out[cid] = (v, item, f"kind={kind} cur={brief(cur)} prev={brief(prev)}")
if __name__ == "__main__":
    from collections import Counter
    print(args[0].split("/")[-1], mode, dict(Counter(v[0] for v in out.values())))
    for cid, (v, item, why) in out.items():
        print(f"  {cid} gold={GOLD[cid]} {v} {item} {why}")
```
