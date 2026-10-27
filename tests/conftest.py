import json
import os

import httpx
import pytest

from app import settings  # loads .env first, so the overrides below win over it

# Tests never touch the project database or the paid Model API (D-016): DATABASE_URL points at
# <name>_test (or TEST_DATABASE_URL), and the key is blanked so no test can make a billed call.
TEST_DATABASE_URL = settings.testing_database_url()
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["MODEL_API_KEY"] = ""

from app.main import create_app  # noqa: E402
from app.settings import ROOT  # noqa: E402
from app.store import MemoryStore  # noqa: E402

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


def pytest_configure(config):
    url = os.environ.get("DATABASE_URL")
    if url and not settings.is_test_database(url):
        pytest.exit("refusing to run: DATABASE_URL is not a test database (name must end in _test)", 1)


@pytest.fixture(scope="session")
def pg_url():
    """The test database, created and migrated on first use; skips (never falls back) if PostgreSQL is down."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set (run make infra-up)")
    assert settings.is_test_database(url)
    import psycopg

    from app.migrate import ensure_database, migrate

    try:
        ensure_database(url)
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable ({type(exc).__name__}); run make infra-up")
    migrate(url)
    return url
