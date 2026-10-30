"""The measured session's attachment binding: credentials from the environment, the trial app registers the
binding at startup, the runner's children and generic event posts carry the credentials, logs never do."""

import json

import httpx
import pytest

from app.attachment_auth import SOURCE_KINDS, token_digest
from app.contracts import make_event
from measure import c5_bind, c5_live, c5_proc

CREDS = {"session": "rescore-bind-1", "epoch": "epoch-rescore-bind-1", "source_token": "src-secret-1",
         "system_token": "sys-secret-1"}  # fmt: skip
ENV = {"C5_BIND_SESSION": CREDS["session"], "C5_BIND_EPOCH": CREDS["epoch"],
       "C5_BIND_SOURCE_TOKEN": CREDS["source_token"], "C5_BIND_SYSTEM_TOKEN": CREDS["system_token"]}  # fmt: skip
SECRETS = (CREDS["source_token"], CREDS["system_token"])


def test_no_variables_means_no_binding():
    assert c5_bind.credentials({}) is None
    assert c5_bind.credentials({"C5_BIND_SESSION": "", "OTHER": "x"}) is None


def test_all_four_variables_give_the_credentials():
    assert c5_bind.credentials(ENV) == CREDS


@pytest.mark.parametrize("missing", c5_bind.VARS)
def test_a_partial_set_is_refused(missing):
    with pytest.raises(SystemExit, match="go together"):
        c5_bind.credentials({k: v for k, v in ENV.items() if k != missing})


def test_the_definition_grants_one_source_every_kind_on_sim_and_replay():
    d = c5_bind.definition(CREDS)
    assert (d["session_id"], d["epoch"]) == (CREDS["session"], CREDS["epoch"])
    assert d["system_digest"] == token_digest(CREDS["system_token"])
    (grant,) = d["sources"]
    assert grant["principal"] == "replay-source"
    assert grant["digest"] == token_digest(CREDS["source_token"])
    assert set(grant["kinds"]) == set(SOURCE_KINDS)
    assert all(sorted(v) == ["replay", "sim"] for v in grant["kinds"].values())
    assert not any(secret in json.dumps(d) for secret in SECRETS)


def test_what_may_be_said_holds_no_credential():
    said = c5_bind.public(CREDS)
    assert said == {"session": CREDS["session"], "epoch": CREDS["epoch"], "principal": "replay-source"}
    assert c5_bind.header(CREDS) == {"Authorization": "Bearer " + CREDS["source_token"]}
    assert c5_bind.attachment_auth(CREDS) == {
        CREDS["session"]: {
            "epoch": CREDS["epoch"],
            "source_token": CREDS["source_token"],
            "system_token": CREDS["system_token"],
        }  # fmt: skip
    }


class Registry:
    def __init__(self):
        self.configured = []

    async def configure(self, definition):
        self.configured.append(definition)


class Inner:
    """An ASGI app with a lifespan and an attachment registry on its state."""

    def __init__(self):
        self.state = type("State", (), {})()
        self.state.attachments = Registry()
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope["type"])
        if scope["type"] != "lifespan":
            await send({"type": "http.response.start", "status": 200})
            return
        assert (await receive())["type"] == "lifespan.startup"
        await send({"type": "lifespan.startup.complete"})


async def test_the_wrapper_registers_the_binding_when_startup_completes():
    inner, sent = Inner(), []
    bound = c5_bind.Bound(inner, CREDS)

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        sent.append((message["type"], list(inner.state.attachments.configured)))

    await bound({"type": "lifespan"}, receive, send)
    assert sent == [
        ("lifespan.startup.complete", [c5_bind.definition(CREDS)])
    ]  # registered before completion
    assert bound.registered == c5_bind.public(CREDS)
    await bound({"type": "http"}, receive, send)
    assert inner.scopes == ["lifespan", "http"] and sent[-1][0] == "http.response.start"


async def test_an_unbound_wrapper_registers_nothing():
    inner = Inner()
    assert await c5_bind.Bound(inner).register() is None
    assert inner.state.attachments.configured == []


def test_the_app_entry_point_refuses_to_start_without_the_variables(monkeypatch):
    for var in c5_bind.VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit, match="C5_BIND_"):
        c5_bind.bound_app()


def test_the_worker_child_gets_the_credentials_as_attachment_auth(monkeypatch):
    from worker import runner

    monkeypatch.setattr(runner.Runner, "__init__", runner.Runner.__init__)  # restored after the test
    said = c5_proc.bind("worker", CREDS)
    assert said == {**c5_bind.public(CREDS), "module": "worker", "injected": "Runner(attachment_auth=...)"}
    r = runner.Runner("http://app.test", {}, http=httpx.AsyncClient(base_url="http://app.test"))
    assert r.attachment_auth == c5_bind.attachment_auth(CREDS)


def test_the_media_child_posts_with_the_source_bearer(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", httpx.AsyncClient)  # restored after the test
    said = c5_proc.bind("worker.media", CREDS, "http://app.test/")
    assert said == {**c5_bind.public(CREDS), "module": "worker.media",
                    "injected": "source bearer on httpx clients to http://app.test"}  # fmt: skip
    # the media entry point is re-run by runpy as a fresh __main__: what it gets is the patched client class
    scope = {}
    exec("import httpx\napp = httpx.AsyncClient(base_url='http://app.test', timeout=10)\n"
         "other = httpx.AsyncClient(base_url='http://model.test')", scope)  # fmt: skip
    assert scope["app"].headers["authorization"] == "Bearer " + CREDS["source_token"]
    assert "authorization" not in scope["other"].headers
    with pytest.raises(SystemExit, match="--base-url"):
        c5_proc.bind("worker.media", CREDS, None)


def test_the_base_url_argument_is_read_from_the_childs_arguments():
    assert c5_proc.arg_value(["--session", "s", "--base-url", "http://x"], "--base-url") == "http://x"
    assert c5_proc.arg_value(["--base-url=http://y"], "--base-url") == "http://y"
    assert c5_proc.arg_value(["--base-url"], "--base-url") is None


class Http:
    def __init__(self):
        self.calls = []

    async def post(self, url, json, headers=None):
        self.calls.append((url, [e["event_id"] for e in json], headers))
        return self

    def raise_for_status(self):
        pass


async def test_the_runners_generic_event_posts_carry_the_bearer():
    http, bearer = Http(), c5_bind.header(CREDS)
    comment = {"event_id": "c1", "type": "comment", "ts_ms": 0}
    assert await c5_live.post_comments(http, "run", [(0, comment)], headers=bearer) >= 0
    ts = 1_793_000_000_000
    frame = {
        "event_id": "run.f0",
        "session_id": "run",
        "type": "frame",
        "ts_ms": ts,
        "jpeg_path": "data/x/f.jpg",
    }
    seen = make_event("product_seen", "run", ts, "replay", event_id="run.ps-1-00", category="shoes", confidence=0.9,
                      attributes={"color": None, "size": None, "material": None}, is_new=True, frame_ts_ms=ts)  # fmt: skip
    out = await c5_live.post_seen(http, "run", [(0, seen)], [frame], 0, wait_s=0.1, headers=bearer)
    assert out == {"posted": 1, "no_frame": []}
    assert http.calls == [("/api/sessions/run/events", ["c1"], bearer),
                          ("/api/sessions/run/events", ["run.ps-1-00"], bearer)]  # fmt: skip
    await c5_live.post_comments(http, "run", [(0, comment)])
    assert http.calls[-1][2] is None
