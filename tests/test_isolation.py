"""Tests stay off the project database and the paid Model API; test-session spend is shown apart (D-016)."""

import os
import subprocess
import sys

import pytest

from app import settings
from app.contracts import make_event, now_ms
from app.settings import ROOT
from app.store import PgStore

DEV = "postgresql://u:p@127.0.0.1:55433/keviq"


def model_call(sid, usd, n=1):
    return make_event(
        "model_call", sid, now_ms(), "sim", event_id=f"{sid}.mc{n}-{now_ms()}", model="muse-spark-1.3",
        purpose="test", tokens_in=1, tokens_out=1, cost_usd=usd, latency_ms=1, ok=True,
    )  # fmt: skip


def test_test_database_name(monkeypatch):
    assert settings.is_test_database(DEV + "_test") and not settings.is_test_database(DEV)
    assert not settings.is_test_database("postgresql://u:p@h/keviq_test_copy")
    assert not settings.is_test_database(None)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", DEV + "?sslmode=disable")
    assert settings.testing_database_url() == DEV + "_test?sslmode=disable"
    monkeypatch.setenv("DATABASE_URL", DEV + "_test")
    assert settings.testing_database_url() == DEV + "_test"
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://u:p@h/other_test")
    assert settings.testing_database_url() == "postgresql://u:p@h/other_test"


def test_this_run_is_isolated():
    url = os.environ.get("DATABASE_URL")
    assert url is None or settings.is_test_database(url)
    assert not os.environ.get("MODEL_API_KEY")


def test_pytest_refuses_a_non_test_database():
    env = {**os.environ, "TEST_DATABASE_URL": DEV}
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_contracts.py"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert run.returncode == 1, run.stdout[-500:]
    assert "not a test database" in run.stdout + run.stderr


async def test_cost_counts_test_sessions_in_the_cap_and_shows_them_apart(mem_app):
    _, store, client = mem_app
    await store.append(model_call("pytest-a", 0.25))
    await store.append(model_call("smoke-1", 0.5))
    await store.append(model_call("live-1", 1.0))
    cost = (await client.get("/api/cost")).json()
    assert cost["today_usd"] == pytest.approx(1.75) and cost["total_usd"] == pytest.approx(1.75)
    assert cost["test_sessions"]["today_usd"] == pytest.approx(0.75)
    assert cost["test_sessions"]["total_usd"] == pytest.approx(0.75)


async def test_pg_cost_test_sessions_apart(pg_url):
    store = PgStore(pg_url)
    await store.open()
    try:
        before = (await store.cost_usd(), await store.cost_usd(test_only=True))
        await store.append(model_call("pytest-cost", 0.25))
        await store.append(model_call("live-cost", 1.0))
        after = (await store.cost_usd(), await store.cost_usd(test_only=True))
        assert after[0] - before[0] == pytest.approx(1.25) and after[1] - before[1] == pytest.approx(0.25)
    finally:
        await store.close()
