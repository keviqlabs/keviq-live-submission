"""Settings from the environment; a repo-root `.env` fills in values not already set (never printed)."""

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def env_usd(name: str) -> float | None:
    """An optional USD amount: unset or empty is None (off)."""
    raw = env(name)
    return None if raw is None or raw.strip() == "" else float(raw)


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def database_url() -> str | None:
    return env("DATABASE_URL")


def is_test_database(url: str | None) -> bool:
    """True for a postgresql:// URL whose database name ends in `_test` (D-016)."""
    if not url:
        return False
    parts = urlsplit(url)
    return parts.scheme in ("postgres", "postgresql") and parts.path.lstrip("/").endswith("_test")


def testing_database_url() -> str | None:
    """Database for tests and smoke: TEST_DATABASE_URL, else DATABASE_URL with `_test` added to the name."""
    url = env("TEST_DATABASE_URL")
    if url:
        return url
    base = database_url()
    if not base or is_test_database(base):
        return base
    parts = urlsplit(base)
    name = parts.path.lstrip("/")
    return urlunsplit(parts._replace(path=f"/{name}_test")) if name else None
