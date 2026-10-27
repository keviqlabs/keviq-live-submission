"""Apply migrations/*.sql in filename order (idempotent). `python -m app.migrate`"""

import sys

import psycopg

from app.settings import ROOT, database_url


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


if __name__ == "__main__":
    url = database_url()
    if not url:
        sys.exit("DATABASE_URL is not set (see deploy/.env.example)")
    names = migrate(url)
    print("migrations applied:", ", ".join(names) if names else "none (up to date)")
