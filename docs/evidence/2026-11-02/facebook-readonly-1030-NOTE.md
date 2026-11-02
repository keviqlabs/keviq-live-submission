# Facebook comment source, read-only, one poll — page-test-1030 — 30 October 2026

Written on 2 November 2026 from the files of the run, which stay outside Git (sha256 below). No Page name, Page ID,
video ID or token is recorded here.

## What ran
- Code: `e18c5f94e101acf0d2eab8aa065dce498fd6bee0` (merge of PR #69, the read-only Facebook comment source and the `live-ingest` entry point).
- Command: the `connectors.facebook.ingest` entry point (`make live-ingest`) for one poll, into session `page-test-1030`. Log: `live-ingest: read-only, one poll every 2s -> session page-test-1030`, then `done: 1 comments posted`.
- Result file: rc 0, elapsed 1.11 s, `model_key_empty: true`, `cohost_enabled: false`.
- Source: an already-ended live video on the author's own Page. The connector sends only GET requests to Facebook (docstring of `connectors/facebook/ingest.py`), so nothing was sent to Facebook and nothing was hidden.

## What the app showed afterwards (`app-readback.json`)
- comments 1, cards 0, orders 0, approval_queue 0.
- Model cost today 0.0 USD. The model key was empty, so no model call was possible.

## What this does not show
- The connector has not been run on a live broadcast in progress.
- One comment is one comment: no rate, latency or volume figure comes from this run.
- No order, card, reply or hide path ran on Facebook data.

## Files (outside Git, sha256)

| File | Bytes | sha256 |
|---|---|---|
| `access-refreshed.png` | 18544 | `04cf320490a01d8f2790795b1c85f8dfc3815a46fbacb98e3919dce023c839ed` |
| `app-readback.json` | 139 | `d10d459d81861a97ab51727e168696ac26e4ba0be48d7bf0ff165b6bf4422f0e` |
| `console-result.png` | 68194 | `a5d9e77e9968dc58ee9c282a440478de9897f699708ad0acde11a2bc7b2abf69` |
| `ingest-once-result.json` | 180 | `406386ee5d9f43d5244efe0e6e86e519bd019fcfbb63cdbb01573b78833ba25a` |
| `ingest-once.log` | 146 | `4f8511b87f34c04d5fc3ff80e23de6d14ea785784eb2cdf0377e211c285800f4` |
