# Stored frame completion in the unpaid rehearsal

Scope: the existing no-key C5 offline stand-in only. This changes no production
worker, app admission, contract, order rule, price authority, deadline, catalog,
gold, stored model output or scoring threshold.

## Reproduced defect

At base `ee6630632f6a380ec10985b806666b0b444efd66`, a replay recognition was
already admitted and its normal product handler posted a card, but the worker
had not registered that external recognition as a producer completion. The
no-key frame read therefore left `read_error`. The unchanged server rejects
that terminal when the same frame has an admitted recognition.

A deterministic synthetic reproduction used the real offline installation,
worker processing, authenticated event admission, in-memory store and server
proof validation. Both orderings failed: frame read before product completion,
and frame read after product completion. A positive control referencing the
actual admitted product and card passed. Result before the change:
`2 failed, 1 passed in 0.46s` (exit 1). It made no model or external service call.

## Repair and boundaries

The offline process first reads the same session at the incoming stored
sequence and requires the exact canonical event body, one known source frame
and matching frame time. It re-posts that unchanged event through the normal
authenticated producer. The server's idempotent response retains the original
sequence and source receipt. The unchanged product handler must then complete
and post its actual card consequences before a product terminal exists.

A later failed no-key frame read retains only that already completed terminal;
it still returns `failed` for lane accounting. Missing, refused, malformed or
unsuccessful stored recognition does not become positive evidence. The normal
server still checks source epoch and the entire required frame prefix. An
idempotent re-post cannot renew an earlier epoch's receipt. Installation is
limited to the existing explicit offline/no-key mode; ordinary worker starts
with or without a model key do not install it.

## Focused verification

Commands (local environment, no database or model key):

```sh
uv run ruff check measure/c5_offline.py tests/test_c5_offline.py tests/test_c5_offline_frame_evidence.py
uv run ruff format --check measure/c5_offline.py tests/test_c5_offline.py tests/test_c5_offline_frame_evidence.py
DATABASE_URL= TEST_DATABASE_URL= MODEL_API_KEY= uv run pytest -q -rs -p no:cacheprovider tests/test_c5_offline_frame_evidence.py tests/test_c5_offline.py tests/test_worker_attachment_evidence.py tests/test_attachments_runtime.py
```

Output: `All checks passed!`; three files already formatted;
`77 passed in 1.46s` (exit 0). The 18 new cases include both completion orders,
no duplicate stored event/card side effects, refusal before product dispatch,
failed/empty product handlers, unchanged failed-read accounting, an event after
550 earlier records, retained old-epoch rejection, and installation cleanup.
The large-history test uses the same history endpoint and real accepted
sequence; it does not advance the worker's observed cursor artificially.

This is protocol/component evidence, not a rerun of the two observed expired
comments, a successful live model read, new successful orders, a receipt image
result, or the 500-comment/30-minute acceptance run. Full-suite execution and
independent review are recorded separately against the final commit. The
original failed-read/error counts and unpaid stored-output disclosure remain.
