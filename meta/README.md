# meta/

Thin layer over the Model API (Responses with strict json_schema, SAM, Voice WebSocket, Image). Every call emits a `model_call` event with cost; daily spend cap (the VN day the store recorded the call, `ingested_ms`, not the event's `ts_ms`); retry/backoff on 429.

Per-session cap: with `MODEL_SESSION_CAP_USD` set, a session whose `model_call` spend (all time, `/api/cost?session=<id>` → `session_usd`) has reached it makes no more calls: `BudgetExceeded("session_cap")`, a `model_call` with `ok=false, error=session_cap` in that session, no request sent. `MODEL_SESSION_WARN_USD` logs a warning once per session. Unset, nothing is checked per session. The spend is read before each call and nothing is reserved, so calls already in flight when the cap is crossed can still take the session over it; a voice stream is checked once, when it opens.
