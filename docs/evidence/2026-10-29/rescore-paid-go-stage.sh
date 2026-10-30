#!/bin/bash
# go-stage.sh <seconds> [run]: start the wrapper for one stage and watch rule (d) until the runner is gone.
OUT=$(cd "$(dirname "$0")" && pwd); SECS=$1; RUN=${2:-rescore-paid-$(date +%m%d-%H%M)}
echo "$RUN" > "$OUT/current-run"
bash "$OUT/rescore-paid-run.sh" "$RUN" "$OUT" "$SECS" > "$OUT/$RUN-wrapper.log" 2>&1 &
WR=$!; start=$(date +%s); W="$OUT/$RUN-watch.log"; echo "$(date +%T) stage $RUN seconds $SECS wrapper pid $WR" > "$W"
while kill -0 $WR 2>/dev/null; do
  sleep 10
  applog="$OUT/$RUN-app.log"; [ -f "$applog" ] || continue
  cov=$(grep -c 'covered=True' "$applog"); ev=$(grep -c 'attachment evaluation' "$applog")
  bad=$(grep -cE '/attachments/system HTTP/1.1" (401|422)' "$applog")
  cost=$(curl -s --max-time 2 "http://127.0.0.1:8040/api/cost?session=$RUN")
  usd=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("session_usd",0))' "$cost" 2>/dev/null || echo 0)
  rows=$(docker exec keviq-live-postgres-1 psql -U keviq -d keviq_paid_test -At -F: -c "SELECT COALESCE(body->>'outcome','?'), count(*) FROM events WHERE type='comment_attachment' AND session_id='$RUN' GROUP BY 1" 2>/dev/null)
  tot=0; exp=0
  while IFS=: read -r o n; do [ -n "$n" ] || continue; tot=$((tot+n)); [ "$o" = expired ] && exp=$((exp+n)); done <<< "$rows"
  el=$(( $(date +%s) - start ))
  echo "$(date +%T) t=$el eval=$ev cov=$cov bad=$bad usd=$usd attachments=$tot expired=$exp [$(echo $rows | tr '\n' ' ')]" >> "$W"
  stop=""
  [ "$tot" -ge 10 ] && [ "$exp" -ge 10 ] && [ "$cov" -eq 0 ] && stop="10 attachments, all expired, no covered=True"
  [ "$bad" -gt 0 ] && stop="401/422 on /attachments/system"
  if python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) >= 6 else 1)' "$usd" 2>/dev/null && [ "$el" -lt 230 ]; then stop="6 USD before 200 s of loop"; fi
  if [ -n "$stop" ]; then echo "$(date +%T) STOP: $stop -> SIGTERM to the runner" >> "$W"; pkill -TERM -f "measure.c5 --base-url http://127.0.0.1:8040"; sleep 15; fi
done
wait $WR; rc=$?; echo "$(date +%T) wrapper rc=$rc" >> "$W"; echo "wrapper rc=$rc run $RUN"
