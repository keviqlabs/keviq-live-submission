"""Aggregate-only summary of one C5 run file: ids and numbers, no text, no media."""
import hashlib
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
d = json.loads(path.read_text())
rf, sc = d["run_file"], d["score"]
c5 = sc["c5"]
out = {
    "run": rf["run"],
    "sha": rf["sha"],
    "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "config": {k: rf["config"].get(k) for k in ("mode", "base_url_port", "healthz", "paid", "seconds", "cap_usd")},
    "cost_api_session_usd": rf["cost_api"].get("session_usd"),
    "ledger_model_call_rows": rf["ledger"]["rowcount"],
    "code_checks_rc": sorted({v["rc"] for v in rf["code_checks"].values()}),
    "posted": sc["posted"],
    "orders": {k: c5[k] for k in ("order_recall", "order_n", "receipts", "receipts_ok", "phone_n", "phone_visible_max_s")},
    "receipts": [{k: r.get(k) for k in ("order_id", "version_ok", "image", "image_path", "missing")}
                 for r in sc["receipts"]["rows"]],
    "receipts_keys": sorted({k for r in sc["receipts"]["rows"] for k in r}),
    "references": {k: sc["references"][k] for k in ("n", "right", "accuracy", "gate")},
    "situations": {k: (v["status"], v["triggered"], v["passed"]) for k, v in sc["situations"].items()},
    "per_loop": [{k: p.get(k) for k in ("loop", "comments", "orders", "confirmed", "cancelled", "needs_seller")}
                 for p in sc["per_loop"]],
}
print(json.dumps(out, indent=1, ensure_ascii=False))
