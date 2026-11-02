# Results recount and clean-clone timing — 3 November 2026

Written on 3 November for `docs/RESULTS.md`, on main `2a3ba2e`. Every number here comes from one of four sources,
each with its command and output below: read-only `SELECT` queries on the ledger database `keviq_paid_test`, a
recount of the data files in this repository, the offline `measure` run on a stored events export, and the
clean-clone log in this folder. No model was called for this NOTE. Times are in ICT.

## Files
| File | Bytes | sha256 |
|---|---|---|
| `cleanclone-1103.log` | 2827 | `b3f0335f47bb8fcf66d0c6fe22ba3fc51bbd35f10911d7c8e7a1ff3e8a4fe706` |
| `cleanclone-1103.sh` | 1608 | `7732d001e0ca508dd9999e3af9510e0f4e9fa657d89e89b5c18ac4aac4e1cc77` |

Both are copies of files kept outside the repository (log sha256
`f42ca3cdc5dcff882b1694a2cfbddd01760d0dbf50ca58c3fc0c8426d654058d`, script sha256
`745c1d1eae84c4905a1dcae5ca51384070b473ac31cd40a36d77b81bcb8a91bf`). The only change is that machine paths are
replaced by `<clone>`, `<uv-cache>`, `<log>`, `<app-log>` and `<python3.12>`; the rest is verbatim. With those
placeholders the script does not run as stored.

## Clean clone (one run)
- Code: `2a3ba2e30efa03ac592370b6aa612ce8297ba1c6` (log line 5). Start 2026-11-03 00:54:33 ICT (epoch 1793642073).
- What the script does (`cleanclone-1103.sh`): clone with `gh repo clone`, `uv sync` with an emptied uv cache,
  `make infra-up` in its own compose project on port 55499, `make app` on port 8072, `make replay SESSION=judge1`,
  then one request to the console page and to `/healthz`, then teardown. The compose project and ports differ from
  the README defaults so that another database container on the machine was not touched.
- Step times, from the epoch stamps in the log:

```
$ awk '/^17936[0-9]+ /{ if (t0 == "") { t0 = $1; prev = $1 } printf "+%d s (step %d s) %s\n", $1 - t0, $1 - prev, $0; prev = $1 }' cleanclone-1103.log
+0 s (step 0 s) 1793642073 start
+3 s (step 3 s) 1793642076 clone rc=0
+4 s (step 1 s) 1793642077 uv-sync rc=0
+10 s (step 6 s) 1793642083 infra-up rc=0
+13 s (step 3 s) 1793642086 app-ready healthz={"ok":true,"store":"pg","head_seq":0}
+77 s (step 64 s) 1793642150 replay rc=0
+77 s (step 0 s) 1793642150 console sid=judge1-1103-005446 http=200 healthz={"ok":true,"store":"pg","head_seq":69}
+79 s (step 2 s) 1793642152 teardown rc=0
```

- Other log lines: `Prepared 38 packages in 1.47s` (line 19); `replay data/sessions/judge1/events.jsonl: 69 events
  -> judge1-1103-005446` (line 74); `done: 69 events posted` (line 76).
- Clone to console page answering 200 with `head_seq` 69: 77 s. The stamps have one-second resolution.
- Caveats:
  - The `postgres:17-alpine` image was already on the machine (log line 1, `image cached: sha256:266d3c257594`);
    a first pull is not in the 6 s of `infra-up`.
  - Python 3.12 was already installed (log line 6, `Using CPython 3.12.13 interpreter at: <python3.12>`). The
    README notes that a first `uv sync` downloads Python 3.12; that download is not in the 1 s.
  - The uv package cache was emptied first, so the 38 packages were downloaded; the network speed was not measured.
  - One run on one machine. Reading the README and opening a second terminal are not in the time.
  - 64 of the 77 s are the replay itself, which posts the judge1 events on their own clock:

```
$ jq -s '[.[].ts_ms] | {n: length, span_s: ((max - min)/1000)}' data/sessions/judge1/events.jsonl
{
  "n": 69,
  "span_s": 61.8
}
```

## Ledger (read-only `SELECT` on `keviq_paid_test`)
Run with `docker exec -i <postgres container> psql -U keviq -d keviq_paid_test < <file>.sql`, rc 0 each.

Full-size runs 4, 5 and 6:

```sql
SELECT session_id, count(*) AS calls, sum((body->>'cost_usd')::numeric) AS usd FROM events
 WHERE session_id IN ('c5-paid-1102-1532','c5-paid-1102-1709','c5-paid-1102-2141-r8') AND type='model_call' GROUP BY 1 ORDER BY 1;
SELECT session_id, body->>'purpose' AS purpose, body->>'error' AS error, count(*) FROM events
 WHERE session_id IN ('c5-paid-1102-1532','c5-paid-1102-1709','c5-paid-1102-2141-r8') AND type='model_call' AND coalesce(body->>'ok','true')<>'true' GROUP BY 1,2,3 ORDER BY 1;
SELECT session_id, count(*) FILTER (WHERE body->>'purpose'='product_seen') AS product_seen_calls FROM events
 WHERE session_id IN ('c5-paid-1102-1532','c5-paid-1102-1709','c5-paid-1102-2141-r8') AND type='model_call' GROUP BY 1 ORDER BY 1;
SELECT session_id, body->>'outcome' AS outcome, count(*) FROM events
 WHERE session_id IN ('c5-paid-1102-1532','c5-paid-1102-1709','c5-paid-1102-2141-r8') AND type='comment_attachment' GROUP BY 1,2 ORDER BY 1,2;
SELECT session_id, count(*) AS receipts, count(*) FILTER (WHERE (body->'receipt'->>'image_url') IS NOT NULL) AS with_image FROM events
 WHERE session_id IN ('c5-paid-1102-1532','c5-paid-1102-1709','c5-paid-1102-2141-r8') AND type='order' AND jsonb_typeof(body->'receipt')='object' GROUP BY 1 ORDER BY 1;
SELECT session_id, count(DISTINCT body->>'order_id') AS receipt_orders FROM events
 WHERE session_id IN ('c5-paid-1102-1532','c5-paid-1102-1709','c5-paid-1102-2141-r8') AND type='order' AND jsonb_typeof(body->'receipt')='object' GROUP BY 1 ORDER BY 1;
SELECT session_id, state, count(*) FROM (SELECT DISTINCT ON (session_id, body->>'order_id') session_id, body->>'state' AS state FROM events
 WHERE session_id IN ('c5-paid-1102-1532','c5-paid-1102-1709','c5-paid-1102-2141-r8') AND type='order' ORDER BY session_id, body->>'order_id', seq DESC) t GROUP BY 1,2 ORDER BY 1,2;
SELECT session_id, count(DISTINCT body->>'order_id') AS orders FROM events
 WHERE session_id IN ('c5-paid-1102-1532','c5-paid-1102-1709','c5-paid-1102-2141-r8') AND type='order' GROUP BY 1 ORDER BY 1;
SELECT session_id, to_timestamp(min(ts_ms)/1000.0) AT TIME ZONE 'Asia/Ho_Chi_Minh' AS first_ict, to_timestamp(max(ts_ms)/1000.0) AT TIME ZONE 'Asia/Ho_Chi_Minh' AS last_ict, round((max(ts_ms)-min(ts_ms))/1000.0) AS span_s FROM events
 WHERE session_id IN ('c5-paid-1102-1532','c5-paid-1102-1709','c5-paid-1102-2141-r8') GROUP BY 1 ORDER BY 1;
```

```
      session_id      | calls |    usd     
----------------------+-------+------------
 c5-paid-1102-1532    |  2484 | 3.97704250
 c5-paid-1102-1709    |  2536 | 4.25326475
 c5-paid-1102-2141-r8 |  2552 | 4.36595840

      session_id      |   purpose    |   error   | count 
----------------------+--------------+-----------+-------
 c5-paid-1102-1532    | product_seen | cancelled |    17
 c5-paid-1102-1709    | product_seen | cancelled |   129
 c5-paid-1102-2141-r8 | product_seen | cancelled |   176

      session_id      | product_seen_calls 
----------------------+--------------------
 c5-paid-1102-1532    |               1755
 c5-paid-1102-1709    |               1755
 c5-paid-1102-2141-r8 |               1755

      session_id      | outcome | count 
----------------------+---------+-------
 c5-paid-1102-1532    | expired |    14
 c5-paid-1102-1532    | ready   |   137
 c5-paid-1102-1709    | expired |    54
 c5-paid-1102-1709    | ready   |    97
 c5-paid-1102-2141-r8 | expired |    23
 c5-paid-1102-2141-r8 | ready   |   129

      session_id      | receipts | with_image 
----------------------+----------+------------
 c5-paid-1102-1532    |        6 |          0
 c5-paid-1102-1709    |        9 |          9
 c5-paid-1102-2141-r8 |        7 |          7

      session_id      | receipt_orders 
----------------------+----------------
 c5-paid-1102-1532    |              6
 c5-paid-1102-1709    |              9
 c5-paid-1102-2141-r8 |              7

      session_id      |    state     | count 
----------------------+--------------+-------
 c5-paid-1102-1532    | CANCELLED    |    12
 c5-paid-1102-1532    | CONFIRMED    |     6
 c5-paid-1102-1532    | CONFIRM_SENT |    74
 c5-paid-1102-1532    | NEEDS_SELLER |     1
 c5-paid-1102-1709    | CANCELLED    |     8
 c5-paid-1102-1709    | CONFIRMED    |     9
 c5-paid-1102-1709    | CONFIRM_SENT |    55
 c5-paid-1102-2141-r8 | CANCELLED    |    12
 c5-paid-1102-2141-r8 | CONFIRMED    |     7
 c5-paid-1102-2141-r8 | CONFIRM_SENT |    66

      session_id      | orders 
----------------------+--------
 c5-paid-1102-1532    |     93
 c5-paid-1102-1709    |     72
 c5-paid-1102-2141-r8 |     85

      session_id      |        first_ict        |        last_ict         | span_s 
----------------------+-------------------------+-------------------------+--------
 c5-paid-1102-1532    | 2026-11-02 15:32:09.722 | 2026-11-02 16:04:14.267 |   1925
 c5-paid-1102-1709    | 2026-11-02 17:09:43.065 | 2026-11-02 17:42:57.269 |   1994
 c5-paid-1102-2141-r8 | 2026-11-02 21:42:13.876 | 2026-11-02 22:14:17.288 |   1923
```

Messages the shop sent, and the labels of the five scripted cancel comments (`c5-1-068`, `c5-2-038`, `c5-3-032`,
`c5-4-038`, `c5-5-039`), in every full-size run of 2 November:

```sql
SELECT session_id, body->>'template' AS template, count(*) FROM events
 WHERE session_id LIKE 'c5-paid-1102-%' AND type='message_out' GROUP BY 1,2 ORDER BY 1,2;
SELECT session_id, count(*) AS message_in FROM events
 WHERE session_id LIKE 'c5-paid-1102-%' AND type='message_in' GROUP BY 1 ORDER BY 1;
SELECT session_id, body->>'label' AS label, body->>'action' AS action, count(*) FROM events
 WHERE session_id IN ('c5-paid-1102-1532','c5-paid-1102-1709','c5-paid-1102-2141-r8') AND type='comment_labeled'
   AND body->>'comment_id' IN ('c5-1-068','c5-2-038','c5-3-032','c5-4-038','c5-5-039') GROUP BY 1,2,3 ORDER BY 1,2,3;
```

```
      session_id      |     template     | count 
----------------------+------------------+-------
 c5-paid-1102-1028    | answer_question  |   124
 c5-paid-1102-1028    | ask_variant      |     2
 c5-paid-1102-1028    | confirm_order    |    68
 c5-paid-1102-1028    | order_cancelled  |     5
 c5-paid-1102-1028    | receipt          |     6
 c5-paid-1102-1028    | waiting_for_shop |    31
 c5-paid-1102-1426    | answer_question  |   122
 c5-paid-1102-1426    | confirm_order    |    12
 c5-paid-1102-1426    | order_cancelled  |    14
 c5-paid-1102-1426    | waiting_for_shop |    27
 c5-paid-1102-1532    | answer_question  |   120
 c5-paid-1102-1532    | ask_variant      |     4
 c5-paid-1102-1532    | confirm_order    |    83
 c5-paid-1102-1532    | order_cancelled  |    12
 c5-paid-1102-1532    | receipt          |     6
 c5-paid-1102-1532    | waiting_for_shop |    39
 c5-paid-1102-1709    | answer_question  |   120
 c5-paid-1102-1709    | ask_variant      |     4
 c5-paid-1102-1709    | confirm_order    |    66
 c5-paid-1102-1709    | order_cancelled  |     8
 c5-paid-1102-1709    | receipt          |     9
 c5-paid-1102-1709    | waiting_for_shop |    26
 c5-paid-1102-2141-r8 | answer_question  |   118
 c5-paid-1102-2141-r8 | ask_variant      |     4
 c5-paid-1102-2141-r8 | confirm_order    |    76
 c5-paid-1102-2141-r8 | order_cancelled  |    12
 c5-paid-1102-2141-r8 | receipt          |     7
 c5-paid-1102-2141-r8 | waiting_for_shop |    40

      session_id      | message_in 
----------------------+------------
 c5-paid-1102-1028    |          8
 c5-paid-1102-1532    |          9
 c5-paid-1102-1709    |         11
 c5-paid-1102-2141-r8 |         10

      session_id      |   label   | action | count 
----------------------+-----------+--------+-------
 c5-paid-1102-1532    | complaint | none   |     4
 c5-paid-1102-1532    | other     | none   |     1
 c5-paid-1102-1709    | complaint | none   |     3
 c5-paid-1102-1709    | other     | none   |     2
 c5-paid-1102-2141-r8 | complaint | none   |     4
 c5-paid-1102-2141-r8 | other     | none   |     1
```

No `ask_cancel` message in any of the five sessions.

The two judge1 runs:

```sql
SELECT session_id, count(*) AS events FROM events WHERE session_id IN ('judge-paid-1102-2131','judge-paid-1103-0019') GROUP BY 1 ORDER BY 1;
SELECT session_id, count(*) AS calls, sum((body->>'cost_usd')::numeric) AS usd FROM events
 WHERE session_id IN ('judge-paid-1102-2131','judge-paid-1103-0019') AND type='model_call' GROUP BY 1 ORDER BY 1;
SELECT session_id, body->>'purpose' AS purpose, body->>'error' AS error, body->>'latency_ms' AS latency_ms, body->>'cost_usd' AS cost_usd FROM events
 WHERE session_id IN ('judge-paid-1102-2131','judge-paid-1103-0019') AND type='model_call' AND coalesce(body->>'ok','true')<>'true' ORDER BY 1, seq;
SELECT session_id, count(*) FILTER (WHERE jsonb_typeof(body->'receipt')='object') AS receipt_rows, count(*) FILTER (WHERE body->>'state'='CONFIRMED') AS confirmed_rows FROM events
 WHERE session_id IN ('judge-paid-1102-2131','judge-paid-1103-0019') AND type='order' GROUP BY 1 ORDER BY 1;
SELECT session_id, count(DISTINCT body->>'order_id') FILTER (WHERE type='order') AS orders,
       count(*) FILTER (WHERE type='comment_attachment') AS attachments,
       count(*) FILTER (WHERE type='model_call' AND body->>'purpose'='product_seen') AS product_seen_calls
  FROM events WHERE session_id IN ('judge-paid-1102-2131','judge-paid-1103-0019') GROUP BY 1 ORDER BY 1;
```

```
      session_id      | events 
----------------------+--------
 judge-paid-1102-2131 |    170
 judge-paid-1103-0019 |    173

      session_id      | calls |    usd     
----------------------+-------+------------
 judge-paid-1102-2131 |    44 | 0.06632745
 judge-paid-1103-0019 |    46 | 0.06761595

      session_id      |   purpose    |   error   | latency_ms | cost_usd 
----------------------+--------------+-----------+------------+----------
 judge-paid-1103-0019 | product_seen | cancelled | 9935       | 0.0

      session_id      | receipt_rows | confirmed_rows 
----------------------+--------------+----------------
 judge-paid-1102-2131 |            0 |              0
 judge-paid-1103-0019 |            0 |              0

      session_id      | orders | attachments | product_seen_calls 
----------------------+--------+-------------+--------------------
 judge-paid-1102-2131 |      5 |           0 |                 24
 judge-paid-1103-0019 |      6 |           0 |                 27
```

## Recount of the data files (at `2a3ba2e`)
The C5 order comments and the right-item denominator (`counted()` in `measure/c5_score.py`, lines 42-45):

```
$ cat data/comments/c5/stream-*.jsonl | jq -s -c '[.[]|select(.label=="order")] | {order_comments: length, counted_n: ([.[]|select((.order.product|not|not) and ((.order.reference_kind=="current_card") or (.order.reference_kind=="previous_card")))]|length), no_product: ([.[]|select(.order.product|not)]|length)}'
{"order_comments":150,"counted_n":140,"no_product":10}
$ cat data/comments/c5/stream-*.jsonl | jq -r 'select(.label=="order") | "\(.order.product != null) \(.order.reference_kind)"' | sort | uniq -c
  10 false unresolved
 120 true current_card
  20 true previous_card
$ cat data/comments/c5/stream-*.jsonl | jq -r 'select(.label=="order" and .order.product==null) | .expected_action' | sort | uniq -c
  10 ask_customer
```

The scripted customer replies (each waits for the named shop message for its order, `measure/c5_shop.py` lines
330-333):

```
$ cat data/comments/c5/messenger-*.jsonl | wc -l
      20
$ cat data/comments/c5/messenger-*.jsonl | jq -r '[.after, .expect, (.quick_reply // "none"), .situation] | @tsv' | sort | uniq -c
   5 ask_cancel	CANCELLED	CANCEL	8
  10 confirm_order	CONFIRMED	CONFIRM	12
   5 confirm_order	NEEDS_SELLER	none	10
```

So 15 replies wait for a `confirm_order` message and 10 of them are the `CONFIRM` quick reply; 5 wait for an
`ask_cancel` message.

The five `ask_cancel` rows are `c5-1-m04`, `c5-2-m02`, `c5-3-m02`, `c5-4-m03` and `c5-5-m02`; their comments are
`c5-1-068`, `c5-2-038`, `c5-3-032`, `c5-4-038` and `c5-5-039`.

## Offline measure on the judge-paid-1103-0019 export
The export is kept outside the repository (173 rows, 80674 bytes, sha256
`f28d4970d963db3d3a0e09c774619ced0fcb1954d76059e7dcdaa67df9abd726`, the same file as in
`docs/evidence/2026-11-02/judge1-demo1-paid-1102-NOTE.md`, section "Later run: judge-paid-1103-0019"). No key set,
no model call:

```
$ env -u MODEL_API_KEY uv run python -m measure --events-file <export> --gold data/sessions/judge1/gold.json --no-write
(rc 0; key lines)
events: 173  (hand-written fx- fixtures: 0; numbers over fixtures are not model results)
C1 consequence within 5000 ms: 11/15  missing=['comment_labeled_b692c0caf0ba45ab951e', 'comment_labeled_b0549dff9a73477e9b46', 'comment_labeled_b6a892ecc3b8402bb2d2', 'comment_labeled_96baf6960ec14ddb8f9f']
C3 referenced_card_id accuracy: 0.143 on n=7 (target >= 0.9 on 50)
C5 order recall: 1.000 on n=7 (target >= 0.9)
C5 seller interventions: 0 in 1.08 min = 0.0/h (target <= 10/h)
C5 phone visible max: 0.0 s on n=1 (target <= 5 s)
C5 receipts with image and version price: 0/0 (target 100%)
labels micro-F1: 0.857 (14/14 labeled)
```

C3 compares `referenced_card_id` literally with the gold ids (`measure/core.py` lines 96-102;
`data/sessions/judge1/gold.json` uses `card-a`, `card-b`, `card-d`), while a live run names its cards
`card-<hash>`.

## Not re-derived here
- `c5-paid-1102-2141-r8`: right item 86 of 140 (0.614), 49 seller interventions (91.7 per hour) and the p95 times
  are copied from `docs/DECISIONS.md` (D-042). The run's score file is not in this repository and the run was not
  re-scored. The orders figure for it (85) is the ledger's distinct `order_id` count; for Runs 4 and 5 the same
  ledger count equals the score's "order created" tier (93 and 72), which was not checked for this run.
- Runs 4 and 5: the five tiers, interventions and runner wall times are copied from their NOTEs,
  `docs/evidence/2026-11-02/c5-paid-1102-1532-NOTE.md` and `docs/evidence/2026-11-02/c5-paid-1102-1709-NOTE.md`.
- Right card 6 of 7 (`judge-paid-1102-2131`) and 7 of 7 (`judge-paid-1103-0019`) are counted by hand in
  `docs/evidence/2026-11-02/judge1-demo1-paid-1102-NOTE.md`; not recounted here.
