"""Read-only ledger reconciliation for one session: c5_ledger_sql.py (kept as is, earlier NOTEs pin its sha256)
plus the rows that are not ok as their own columns. Numbers only; the DB URL is never printed.

A call that was cancelled (the worker's time limit) or got no reply is logged with ok false and cost 0, but the
provider may still bill it: the Billing reconciliation matches `rows_ok` / `cost_usd_ok` and counts `not_ok`
apart, by purpose and by error code (the code's first word only).

  (repo root) PYTHONPATH=. uv run python c5_ledger_sql_v2.py RUN > RUN-ledger.json
"""

import json
import sys

import psycopg
from dotenv import dotenv_values

from app.contracts import now_ms
from app.store import vn_day_bounds

run = sys.argv[1]
url = dotenv_values(".env")["DATABASE_URL"]  # run from the repo root
if not url.rstrip("/").endswith("/keviq"):
    sys.exit("refusing: not the keviq ledger")
day_start, day_end = vn_day_bounds(now_ms())
NOT_OK = "(body->>'ok')::boolean IS NOT TRUE"
COST = "COALESCE(SUM((body->>'cost_usd')::numeric), 0)"
with psycopg.connect(url) as conn:
    conn.read_only = True
    q = (
        f"SELECT count(*) AS n, {COST} AS usd,"
        " count(*) FILTER (WHERE ingested_ms < %(s)s OR ingested_ms >= %(e)s) AS outside_today,"
        " COALESCE(SUM((body->>'cost_usd')::numeric) FILTER (WHERE ingested_ms >= %(s)s AND ingested_ms < %(e)s), 0)"
        f" AS usd_today, count(*) FILTER (WHERE {NOT_OK}) AS not_ok,"
        " min(ingested_ms) AS first_ms, max(ingested_ms) AS last_ms,"
        f" COALESCE(SUM((body->>'cost_usd')::numeric) FILTER (WHERE NOT ({NOT_OK})), 0) AS usd_ok,"
        f" COALESCE(SUM((body->>'cost_usd')::numeric) FILTER (WHERE {NOT_OK}), 0) AS usd_not_ok"
        " FROM events WHERE type = 'model_call' AND session_id = %(r)s"
    )
    row = conn.execute(q, {"s": day_start, "e": day_end, "r": run}).fetchone()
    by = conn.execute(
        f"SELECT body->>'purpose' AS p, count(*), {COST}, count(*) FILTER (WHERE {NOT_OK})"
        " FROM events WHERE type = 'model_call' AND session_id = %s GROUP BY 1 ORDER BY 1",
        (run,),
    ).fetchall()
    errors = conn.execute(
        f"SELECT body->>'purpose' AS p, split_part(COALESCE(body->>'error', 'none'), ' ', 1) AS e, count(*), {COST}"
        f" FROM events WHERE type = 'model_call' AND session_id = %s AND {NOT_OK} GROUP BY 1, 2 ORDER BY 1, 2",
        (run,),
    ).fetchall()
n, usd, outside, usd_today, not_ok, first, last, usd_ok, usd_not_ok = row
print(json.dumps({"run": run, "day_bounds_ms": [day_start, day_end], "rows": n, "cost_usd": float(usd),
                  "rows_outside_today": outside, "cost_usd_today": float(usd_today), "not_ok": not_ok,
                  "first_ingested_ms": first, "last_ingested_ms": last,
                  "by_purpose": {p: {"n": c, "cost_usd": float(u), "not_ok": k} for p, c, u, k in by},
                  "rows_ok": n - not_ok, "cost_usd_ok": float(usd_ok), "cost_usd_not_ok": float(usd_not_ok),
                  "not_ok_by_purpose_error": {f"{p}/{e}": {"n": c, "cost_usd": float(u)} for p, e, c, u in errors}},
                 indent=1))  # fmt: skip
