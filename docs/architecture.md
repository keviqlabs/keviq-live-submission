# Architecture

```mermaid
flowchart LR
  subgraph sources[Inputs]
    R[connectors/sim/replay<br/>session file on the clock]
    FB[connectors/facebook<br/>interface only in P0]
    M[connectors/sim/messenger<br/>simulated threads]
  end

  subgraph app[app/ — the only writer]
    I[POST /api/sessions/id/events<br/>validate contract · idempotent on event_id]
    DB[(PostgreSQL<br/>append-only events<br/>seq = SSE cursor)]
    S[SSE /api/events?cursor=<br/>/api/sessions/id/events]
    Q[state · live cards · cost]
    C[console + overlay]
  end

  subgraph worker[worker/ — HTTP client]
    H[handlers by event type<br/>media · vision · voice · brain]
    MC[meta/ client<br/>cost meter · backoff · schema retry]
  end

  API[Model API<br/>Spark · SAM · Voice]
  ME[measure/<br/>C1 C3 C5 C8 cost]

  R --> I
  FB -.-> I
  M --> I
  I --> DB --> S
  DB --> Q --> C
  S --> H --> MC --> API
  MC -- model_call --> I
  H -- consequences: caused_by + timing --> I
  DB --> ME
```

Every event follows `contracts/events/*.schema.json`. A consequence names its trigger in `caused_by` and carries `timing`, which is what `measure/` uses for C1 and C8. Decisions behind this shape: `docs/DECISIONS.md` D-002 to D-006.
