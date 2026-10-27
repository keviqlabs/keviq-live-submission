import json
import os

import httpx
import pytest

from app.main import create_app
from app.settings import ROOT
from app.store import MemoryStore

DEMO = ROOT / "data" / "sessions" / "demo1"


@pytest.fixture
def demo_events() -> list[dict]:
    return [json.loads(x) for x in (DEMO / "events.jsonl").read_text().splitlines() if x.strip()]


@pytest.fixture
def gold() -> dict:
    return json.loads((DEMO / "gold.json").read_text())


@pytest.fixture
async def mem_app():
    """(app, store, client) over the in-memory store; ASGITransport skips lifespan, so the store is set here."""
    store = MemoryStore()
    await store.open()
    app = create_app(store)
    app.state.store = store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield app, store, client


@pytest.fixture
def pg_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set (run make infra-up)")
    try:
        import psycopg

        psycopg.connect(url, connect_timeout=2).close()
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable ({type(exc).__name__}); run make infra-up")
    return url
