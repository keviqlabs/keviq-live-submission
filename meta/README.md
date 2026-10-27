# meta/

Thin layer over the Model API (Responses with strict json_schema, SAM, Voice WebSocket, Image). Every call emits a `model_call` event with cost; daily spend cap (the VN day the store recorded the call, `ingested_ms`, not the event's `ts_ms`); retry/backoff on 429.
