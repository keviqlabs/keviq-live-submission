"""Apply migrations/*.sql in filename order (idempotent).

python -m app.migrate          # DATABASE_URL
python -m app.migrate --test   # the test database (D-016): created if missing, refused unless named *_test
"""

import sys
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from app.settings import ROOT, database_url, is_test_database, testing_database_url


def migrate(url: str) -> list[str]:
    applied = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())"
        )
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


def ensure_database(url: str) -> bool:
    """Create the database named in `url` if it does not exist (through `postgres` on the same server)."""
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    with psycopg.connect(urlunsplit(parts._replace(path="/postgres")), autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            return False
        try:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        except (psycopg.errors.DuplicateDatabase, psycopg.errors.UniqueViolation):  # created meanwhile
            return False
    return True


if __name__ == "__main__":
    test = "--test" in sys.argv[1:]
    url = testing_database_url() if test else database_url()
    if not url:
        sys.exit("DATABASE_URL is not set (see deploy/.env.example)")
    if test:
        if not is_test_database(url):
            sys.exit("refusing: the test database name must end in _test (TEST_DATABASE_URL)")
        if ensure_database(url):
            print("created the test database")
    names = migrate(url)
    print("migrations applied:", ", ".join(names) if names else "none (up to date)")
