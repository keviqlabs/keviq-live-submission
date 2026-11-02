#!/bin/bash
# Watch a full-size paid C5 run and stop it on a stop rule: scripts/c5_paid_watch.sh <run> <out> <db> [port] [container]
# Starts nothing; run it next to scripts/c5_paid_run.sh. It sends SIGTERM to the wrapper (whose trap stops the runner,
# the per-loop children and the app), not a pattern that misses the children.
RUN=$1; OUT=$2; DB=$3; PORT=${4:-8040}; PG=${5:-keviq-live-postgres-1}
W="$OUT/$RUN-watch.log"; start=$(date +%s); echo "$(date +%T) watching $RUN on :$PORT db $DB" > "$W"
WRAPPER=$(pgrep -o -f "c5_paid_run.sh $RUN ") || { echo "no wrapper for $RUN" | tee -a "$W"; exit 1; }
while kill -0 "$WRAPPER" 2>/dev/null; do
  sleep 15
  applog="$OUT/$RUN-app.log"; [ -f "$applog" ] || continue
  cov=$(grep -c 'covered=True' "$applog"); bad=$(grep -cE '/attachments/system HTTP/1.1" (401|422)' "$applog")
  dec=$(grep -cE '/decision HTTP/1.1" (4[0-9][0-9]|5[0-9][0-9])' "$applog"); err=$(grep -c 'Traceback' "$applog")
  ok=$(grep -c '/decision HTTP/1.1" 200' "$applog")
  usd=$(curl -s --max-time 2 "http://127.0.0.1:$PORT/api/cost?session=$RUN" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("session_usd",0))' 2>/dev/null || echo 0)
  rows=$(docker exec "$PG" psql -U keviq -d "$DB" -At -F: -c "SELECT COALESCE(body->>'outcome','?'), count(*) FROM events WHERE type='comment_attachment' AND session_id='$RUN' GROUP BY 1" 2>/dev/null)
  tot=0; exp=0
  while IFS=: read -r o n; do [ -n "$n" ] || continue; tot=$((tot+n)); [ "$o" = expired ] && exp=$((exp+n)); done <<< "$rows"
  el=$(( $(date +%s) - start ))
  echo "$(date +%T) t=$el cov=$cov bad=$bad decision_ok=$ok decision_failed=$dec tracebacks=$err usd=$usd attachments=$tot expired=$exp" >> "$W"
  stop=""
  [ "$tot" -ge 10 ] && [ "$exp" -ge 10 ] && [ "$cov" -eq 0 ] && stop="10 attachments, all expired, no covered=True"
  [ "$bad" -gt 0 ] && stop="401/422 on /attachments/system"
  [ "$dec" -ge 5 ] && [ "$ok" -eq 0 ] && stop="5 failed seller decisions and none accepted: no order can reach a receipt"
  # about 4 USD expected for 5 loops (about 0.12 USD/min); stop at the warn line or at twice the expected rate
  python3 -c 'import sys; u,t=float(sys.argv[1]),int(sys.argv[2]); sys.exit(0 if u >= 6 or (t > 300 and u/t*60 > 0.25) else 1)' "$usd" "$el" \
    && stop="spend $usd USD at $el s (warn line 6 or rate over 0.25 USD/min)"
  if [ -n "$stop" ]; then echo "$(date +%T) STOP: $stop -> SIGTERM to wrapper $WRAPPER" >> "$W"; kill -TERM "$WRAPPER"; break; fi
done
echo "$(date +%T) watch ends" >> "$W"; tail -2 "$W"
