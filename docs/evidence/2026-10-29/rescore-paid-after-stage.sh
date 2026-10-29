#!/bin/bash
# after-stage.sh <run>: the numbers to report after a stage, plus the model_call jsonl and its sha256.
OUT=$(cd "$(dirname "$0")" && pwd); RUN=$1
PSQL="docker exec keviq-live-postgres-1 psql -U keviq -d keviq_paid_test -At"
echo "== wrapper lines"; grep -E '^(cost before|cost after|runner rc|guard|app log|state after|MODEL_API_KEY set)' "$OUT/$RUN-wrapper.log"
echo "== runner progress (last) + SQL"; grep -E 'progress|loop 1|model_call|session cap|\(.*rows?\)|usd' "$OUT/$RUN-runner.log" | tail -8
echo "== comment_attachment outcomes"; $PSQL -F' | ' -c "SELECT COALESCE(body->>'outcome','?') AS outcome, count(*) FROM events WHERE type='comment_attachment' AND session_id='$RUN' GROUP BY 1 ORDER BY 1"
echo "== per comment"; $PSQL -F' | ' -c "SELECT body->>'comment_id', body->>'outcome', body->>'selected_card_id', jsonb_array_length(COALESCE(body->'candidates','[]'::jsonb)), (body->>'deadline_ms')::bigint-(body->>'comment_ingested_ms')::bigint AS wait_ms FROM events WHERE type='comment_attachment' AND session_id='$RUN' ORDER BY seq"
echo "== app log"; echo "attachment evaluation: $(grep -c 'attachment evaluation' "$OUT/$RUN-app.log"); covered=True: $(grep -c 'covered=True' "$OUT/$RUN-app.log"); covered=True resolved=True: $(grep -c 'covered=True resolved=True' "$OUT/$RUN-app.log"); 401/422 on attachments/system: $(grep -cE '/attachments/system HTTP/1.1\" (401|422)' "$OUT/$RUN-app.log")"
echo "== events by type"; $PSQL -F' ' -c "SELECT type, count(*) FROM events WHERE session_id='$RUN' GROUP BY 1 ORDER BY 2 DESC"
echo "== model_call"; $PSQL -F' | ' -c "SELECT body->>'model' AS model, body->>'purpose' AS purpose, count(*), round(SUM((body->>'cost_usd')::numeric),4) AS usd FROM events WHERE type='model_call' AND session_id='$RUN' GROUP BY 1,2 ORDER BY 4 DESC"
$PSQL -c "SELECT count(*) AS n, COALESCE(SUM((body->>'cost_usd')::numeric),0) AS usd FROM events WHERE type='model_call' AND session_id='$RUN'"
$PSQL -c "SELECT row_to_json(e)::text FROM (SELECT seq, event_id, session_id, type, ts_ms, ingested_ms, body FROM events WHERE type='model_call' AND session_id='$RUN' ORDER BY seq) e" > "$OUT/$RUN-model-calls.jsonl"
echo "jsonl: $(wc -l < "$OUT/$RUN-model-calls.jsonl") lines, sha256 $(shasum -a 256 "$OUT/$RUN-model-calls.jsonl" | cut -d' ' -f1)"
echo "== cost endpoint now (app may be down)"; curl -s --max-time 2 "http://127.0.0.1:8040/api/cost?session=$RUN" || echo "(app stopped)"
