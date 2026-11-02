# measure/

`python -m measure --session <id>` prints the measurement table; JSON goes to `docs/evidence/<date>/`.

`manual/` measures the same C5 stream handled by hand, with no assistant (see `manual/README.md`).

## C5 runner

`python -m measure.c5` runs the test1 stream five times in one session and scores it (`measure/c5.py` lists the
`--dry`, `--base-url` and `--rescore` modes). The shop and the customers it simulates live in `measure/c5_shop.py`.

### Known bias: the simulated seller is an oracle

The shop decides from the gold, not from what a seller sees on the console:

- It judges price proposals against `data/comments/c5/windows.json` and the local test1 prices.
- On an order that waits for the seller to pick one of several frozen attachment candidates, it approves the
  candidate whose item the host held when the customer wrote (from `windows.json`), and rejects the order when no
  single candidate is that item. It never picks a wrong candidate. A real seller can, and is slower than the fixed
  4 s tap delay.
- `score.funnel` does not count a seller-picked card as the system's: such an order is not `right_item` and is
  counted under `seller_pick`. Its `confirmed` and `receipt` tiers still rest on the oracle pick, so for those
  orders they are an upper bound on what a real seller would reach.
- When the app refuses that approval with 409 "variant is not unique on the selected card", the shop rejects the
  order and logs `refused` and `reason` on its row; `score.shop.rejected_after_409` counts these. The decision
  request has no variant field (`contracts/requests/order-decision.schema.json`) and the console offers only the
  candidate buttons and reject, so in this build such an order cannot be confirmed by any seller. It is a product
  gap, not a model miss.

### Paid run

`scripts/c5_paid_run.sh` runs the full-size paid C5 run (5 loops, about 33 minutes) against an app it starts;
`scripts/c5_paid_watch.sh` watches it and stops it on a stop rule.

Preconditions. If one is missing, do not start:

1. Clean tree: `git status --porcelain --untracked-files=no` prints nothing. The script refuses otherwise.
2. PR #81 is in the tree: a "Merge pull request #81" subject on the first-parent line, or `PR81_HEAD` set to a
   commit of #81 that is an ancestor of `HEAD`. The script refuses otherwise.
3. A new database whose name ends in `_test` and is not `keviq_test`. The script migrates it.
4. `OTHER_TODAY_USD` and `OTHER_TOTAL_USD`: the model spend today and in total that is not in that database (the
   app's caps only sum their own database). The script sets the day and total caps to 30 and 300 USD minus these,
   and refuses with less than 8 USD left. `MAIN_ENV` is the path of the `.env` that holds the model key; the
   script reads the key from it and prints nothing from it.
5. `data/local/test1/labels.json` is present (it is not in git). The script refuses otherwise.
6. `make check` is green on the same tree, run from a clean environment before any run variable is set in that
   shell: `env -i HOME=$HOME PATH=$PATH make check`.
7. A free port that is not 8000 or 8001.

Run, from the repo root:

```bash
RUN=c5-paid-$(date +%m%d-%H%M); OUT=docs/evidence/<date>; DB=<name>_test; PORT=8040
export MAIN_ENV=<path to .env> OTHER_TODAY_USD=<usd> OTHER_TOTAL_USD=<usd>
# no model call: database, migration, app and caps only; expect "cost pre-flight ok" and rc 0
PREFLIGHT_ONLY=1 bash scripts/c5_paid_run.sh "$RUN-pre" "$OUT" "$DB" "$PORT" > "$OUT/$RUN-pre-wrapper.log" 2>&1
bash scripts/c5_paid_run.sh "$RUN" "$OUT" "$DB" "$PORT" > "$OUT/$RUN-wrapper.log" 2>&1 &
sleep 5; bash scripts/c5_paid_watch.sh "$RUN" "$OUT" "$DB" "$PORT" <postgres container> &
```

Each attempt needs a new run id: the script makes a new binding epoch and two new tokens for it and never prints
them. It writes `$OUT/$RUN-NOTE.txt` (head, branch, `origin/main`, merged pull requests, database name, port)
before anything starts, and the runner records the same tree in `run_file.git`.

Stop:

- `kill -TERM <wrapper pid>` or Ctrl-C in the wrapper's shell. Its trap stops the runner, the per-loop worker and
  media (`measure.c5_proc`), the image guard, then the app (graceful shutdown is bounded at 10 s). Stopping only
  the runner leaves the worker and media running.
- The watch script sends that SIGTERM itself when 10 attachments all expired with none covered, on any 401 or 422
  from `/attachments/system`, on 5 failed seller decisions while none answered 200, or at 6 USD spent or more
  than 0.25 USD per minute after 300 s. It logs to `$OUT/$RUN-watch.log`; a `STOP:` line there means the run failed.
- A stopped run writes no `$RUN.json` and cannot be scored. Run again with a new run id.

Read the result in `$OUT/$RUN.json`:

- `score.funnel.n` is the number of posted gold order comments. `score.funnel.tiers` counts each tier on its own
  over that same n: `intent` (labelled an order), `right_item` (the card the system put on the order is the gold
  item), `order` (an order exists), `confirmed` (it reached CONFIRMED), `receipt` (a CONFIRMED receipt at the
  confirmed card version's price and version, with an image). Report the five numbers apart.
- `score.funnel.chain` counts the comments that pass every tier up to and including that one, and
  `score.funnel.seller_pick` the orders that needed a seller pick between candidates.
- `score.shop` gives the shop's actions, failed calls, `rejected_after_409` and calls by status; `score.git` gives
  the head, whether the tree was dirty, and the merged pull requests.

```bash
python3 -c 'import json,sys; s=json.load(open(sys.argv[1]))["score"]; f=s["funnel"]; print("n", f["n"], "tiers", f["tiers"], "chain", f["chain"], "seller_pick", f["seller_pick"]); print("shop", s["shop"]); print("git", s["git"])' "$OUT/$RUN.json"
```
