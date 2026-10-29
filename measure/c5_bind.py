"""Attachment binding for a measured session, from the environment, for the trial app and the replay runner.

A session takes the media-time attachment flow only through a durable binding registered by trusted bootstrap
code (docs/attachment-runtime.md). This module is that bootstrap for a measurement: `uvicorn measure.c5_bind:app`
wraps the real app and, when the app's lifespan startup completes, registers the binding of the session named
in the environment, with one source grant (principal "replay-source") over every source kind on the sim and
replay transports. The same variables tell the runner (measure/c5_live) and its children (measure/c5_proc) the
credentials: the worker gets `Runner(attachment_auth=...)`, media and the runner's own generic event posts carry
the source bearer. The variables never reach a log or a run file; only the session, the epoch and the principal do.

C5_BIND_SESSION      the measured session (the run name)
C5_BIND_EPOCH        the worker epoch
C5_BIND_SOURCE_TOKEN the source credential
C5_BIND_SYSTEM_TOKEN the system credential (readiness evaluation only)
"""

import os

PRINCIPAL = "replay-source"
VARS = ("C5_BIND_SESSION", "C5_BIND_EPOCH", "C5_BIND_SOURCE_TOKEN", "C5_BIND_SYSTEM_TOKEN")


def credentials(environ=os.environ) -> dict | None:
    """{session, epoch, source_token, system_token} when all four variables are set, None when none is; a
    partial set is an error (the run would be half bound)."""
    got = {v: environ.get(v, "") for v in VARS}
    if not any(got.values()):
        return None
    if not all(got.values()):
        raise SystemExit(
            "C5_BIND_SESSION, C5_BIND_EPOCH, C5_BIND_SOURCE_TOKEN and C5_BIND_SYSTEM_TOKEN go together"
        )
    return {"session": got["C5_BIND_SESSION"], "epoch": got["C5_BIND_EPOCH"],
            "source_token": got["C5_BIND_SOURCE_TOKEN"], "system_token": got["C5_BIND_SYSTEM_TOKEN"]}  # fmt: skip


def definition(creds: dict) -> dict:
    from app.attachment_auth import SOURCE_KINDS, binding_definition

    source = {"token": creds["source_token"], "principal": PRINCIPAL,
              "kinds": {kind: ["sim", "replay"] for kind in sorted(SOURCE_KINDS)}}  # fmt: skip
    return binding_definition(creds["session"], creds["epoch"], creds["system_token"], [source])


def attachment_auth(creds: dict) -> dict:
    """The worker's `Runner(attachment_auth=...)` mapping for the bound session."""
    return {creds["session"]: {k: creds[k] for k in ("epoch", "source_token", "system_token")}}


def header(creds: dict) -> dict:
    return {"Authorization": "Bearer " + creds["source_token"]}


def public(creds: dict) -> dict:
    """What a log or run file may say about the binding: no credential."""
    return {"session": creds["session"], "epoch": creds["epoch"], "principal": PRINCIPAL}


class Bound:
    """ASGI wrapper: the inner app, whose attachment registry gets the binding before startup completes."""

    def __init__(self, inner, creds: dict | None = None):
        self.inner, self.creds = inner, creds
        self.registered = None

    async def register(self) -> dict | None:
        if self.creds is None:
            return None
        await self.inner.state.attachments.configure(definition(self.creds))
        self.registered = public(self.creds)
        return self.registered

    async def __call__(self, scope, receive, send):
        if scope["type"] != "lifespan":
            await self.inner(scope, receive, send)
            return

        async def sending(message):
            if message["type"] == "lifespan.startup.complete":
                await self.register()
            await send(message)

        await self.inner(scope, receive, sending)


def bound_app():
    creds = credentials()
    if creds is None:
        raise SystemExit(
            "measure.c5_bind:app needs the C5_BIND_* variables; without a binding run app.main:app"
        )
    from app.main import app as inner

    return Bound(inner, creds)


def __getattr__(name: str):
    """`uvicorn measure.c5_bind:app` builds the wrapped app on first access (PEP 562)."""
    if name == "app":
        return bound_app()
    raise AttributeError(name)
