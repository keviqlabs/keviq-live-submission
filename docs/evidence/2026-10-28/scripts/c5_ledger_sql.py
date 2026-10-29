"""Read-only ledger reconciliation for one session (coordinator request 10:20, item 3). Numbers only; the DB URL is never printed.

  (repo root) PYTHONPATH=. uv run python c5_ledger_sql.py RUN > RUN-ledger.json
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
with psycopg.connect(url) as conn:
    conn.read_only = True
    q = (
        "SELECT count(*) AS n, COALESCE(SUM((body->>'cost_usd')::numeric), 0) AS usd,"
        " count(*) FILTER (WHERE ingested_ms < %(s)s OR ingested_ms >= %(e)s) AS outside_today,"
        " COALESCE(SUM((body->>'cost_usd')::numeric) FILTER (WHERE ingested_ms >= %(s)s AND ingested_ms < %(e)s), 0)"
        " AS usd_today, count(*) FILTER (WHERE (body->>'ok')::boolean IS NOT TRUE) AS not_ok,"
        " min(ingested_ms) AS first_ms, max(ingested_ms) AS last_ms"
        " FROM events WHERE type = 'model_call' AND session_id = %(r)s"
    )
    row = conn.execute(q, {"s": day_start, "e": day_end, "r": run}).fetchone()
    by = conn.execute(
        "SELECT body->>'purpose' AS p, count(*), COALESCE(SUM((body->>'cost_usd')::numeric), 0)"
        " FROM events WHERE type = 'model_call' AND session_id = %s GROUP BY 1 ORDER BY 1",
        (run,),
    ).fetchall()
n, usd, outside, usd_today, not_ok, first, last = row
print(json.dumps({"run": run, "day_bounds_ms": [day_start, day_end], "rows": n, "cost_usd": float(usd),
                  "rows_outside_today": outside, "cost_usd_today": float(usd_today), "not_ok": not_ok,
                  "first_ingested_ms": first, "last_ingested_ms": last,
                  "by_purpose": {p: {"n": c, "cost_usd": float(u)} for p, c, u in by}}, indent=1))  # fmt: skip
