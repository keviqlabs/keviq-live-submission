"""The C5 live runner takes its session cap and warning from MODEL_SESSION_CAP_USD / MODEL_SESSION_WARN_USD (8 / 6
when unset), says which it runs with, and the app must have the same before loop 1."""

import asyncio
import json
import os
from types import SimpleNamespace

import httpx
import pytest

from app import settings
from measure import c5_live, c5_shop

RUN = "cap-run"
AsyncClient = httpx.AsyncClient  # the real one: start() patches httpx for main


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """A shell that holds a run's C5_BIND_* or MODEL_* variables, or a repo .env, must not change these tests."""
    for k in [k for k in os.environ if k.startswith(("C5_BIND_", "MODEL_"))]:
        monkeypatch.delenv(k)
    monkeypatch.setattr(settings, "load_dotenv", lambda *a, **kw: None)


def app(cap: float, warn: float):
    """The app as the runner sees it before loop 1: the cost of the session, and one event in it, so the runner
    stops at "not empty" once the cap check is behind it (no worker or media is started)."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"head_seq": 1})
        if request.url.path == "/api/cost":
            return httpx.Response(200, json={"session_cap_usd": cap, "session_warn_usd": warn})
        if request.url.path == f"/api/sessions/{RUN}/events":
            ev = {"event_id": "ev-1", "type": "comment", "session_id": RUN}
            return httpx.Response(200, text=f"id: 1\ndata: {json.dumps(ev)}\n\n")
        return httpx.Response(404)

    return handle


def start(monkeypatch, tmp_path, cap: float, warn: float, env: dict[str, str]) -> str:
    """Runs c5_live.main against that app with this env; returns why it stopped."""
    for k in ("MODEL_SESSION_CAP_USD", "MODEL_SESSION_WARN_USD"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    class Client(AsyncClient):
        def __init__(self, **kw):
            super().__init__(transport=httpx.MockTransport(app(cap, warn)), **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    gold = SimpleNamespace(windows={"loop_ms": 1000})  # the local test1 prices are not needed this far
    monkeypatch.setattr(c5_shop.Gold, "load", staticmethod(lambda *a: gold))
    catalog = tmp_path / "catalog.json"
    catalog.write_text("[]")
    with pytest.raises(SystemExit) as stop:
        asyncio.run(c5_live.main("http://app", RUN, 1, catalog, tmp_path / "out", check_code=False))
    return str(stop.value)


def test_the_cap_from_env_is_the_one_the_app_must_have(monkeypatch, tmp_path, capsys):
    env = {"MODEL_SESSION_CAP_USD": "2.00", "MODEL_SESSION_WARN_USD": "1.50"}
    assert start(monkeypatch, tmp_path, 2.0, 1.5, env) == f"{RUN} is not empty"  # past the cap check
    assert "session cap 2.0 USD, warning 1.5 USD (env)" in capsys.readouterr().out
    refused = start(monkeypatch, tmp_path, 8.0, 6.0, env)
    assert refused.startswith(f"app cap/warn for {RUN}: 8.0/6.0") and refused.endswith(
        "start it with MODEL_SESSION_CAP_USD=2 MODEL_SESSION_WARN_USD=1.5")  # fmt: skip


def test_unset_is_8_and_6(monkeypatch, tmp_path, capsys):
    assert start(monkeypatch, tmp_path, 8.0, 6.0, {}) == f"{RUN} is not empty"
    assert "session cap 8.0 USD, warning 6.0 USD (default)" in capsys.readouterr().out
    refused = start(monkeypatch, tmp_path, 2.0, 1.5, {})
    assert refused.endswith("start it with MODEL_SESSION_CAP_USD=8 MODEL_SESSION_WARN_USD=6")


def caps(cap: str | None, warn: str | None) -> dict:
    keys = ("MODEL_SESSION_CAP_USD", "MODEL_SESSION_WARN_USD")
    return {k: v for k, v in zip(keys, (cap, warn), strict=True) if v is not None}


def test_a_cap_the_run_cannot_use_stops_it_before_it_starts():
    assert c5_live.session_cap(caps(" ", "")) == (8.0, 6.0, "default")
    assert c5_live.session_cap(caps("1.5", "1.2")) == (1.5, 1.2, "env")
    for cap, warn in (("2", None), (None, "1.5"), ("two", "1.5"), ("2", "2"), ("2", "3"), ("2", "0")):
        with pytest.raises(SystemExit):
            c5_live.session_cap(caps(cap, warn))
