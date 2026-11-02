"""Start guard for the hosted demo: refuse to start when a model or Page credential is set.

    python deploy/demo/guard.py [--root DIR]

The public demo replays synthetic sessions only: it spends 0 USD and never reaches Facebook. Exit 1, naming the
variable but never printing its value, when MODEL_API_KEY or FACEBOOK_PAGE_TOKEN is non-empty (whitespace
counts), or when DIR/.env exists (the app loads a repo-root .env for any variable the environment does not set).
DIR defaults to $DEMO_ROOT, else the repository root. Exit 0 otherwise.
"""

import argparse
import os
import sys
from pathlib import Path

CREDENTIALS = ("MODEL_API_KEY", "FACEBOOK_PAGE_TOKEN")


def problems(environ: dict, root: Path) -> list[str]:
    out = [f"{name} is set; the demo runs without it" for name in CREDENTIALS if environ.get(name, "") != ""]
    if (root / ".env").exists():
        out.append(f"{root / '.env'} exists; the app would load credentials from it")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="guard")
    default_root = os.environ.get("DEMO_ROOT") or str(Path(__file__).resolve().parent.parent.parent)
    p.add_argument("--root", default=default_root)
    found = problems(dict(os.environ), Path(p.parse_args(argv).root))
    for line in found:
        print(f"demo start guard: refusing to start: {line}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
