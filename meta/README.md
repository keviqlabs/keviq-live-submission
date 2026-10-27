# meta/

Thin layer over the Model API (Responses with strict json_schema, SAM, Voice WebSocket, Image). Every call emits a `model_call` event with cost; daily spend cap; retry/backoff on 429.
