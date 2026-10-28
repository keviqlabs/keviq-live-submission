"""Shared test-database ownership across worktrees: python -m tests.db_lock -- COMMAND ..."""

import json
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.settings import ROOT, is_test_database, testing_database_url

TOKEN_ENV = "KEVIQ_TEST_LOCK_TOKEN"


class LockBusy(RuntimeError):
    pass


def lock_path() -> Path:
    # Every linked worktree resolves the SAME main-checkout .scratch directory.
    common = subprocess.check_output(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT, text=True
    ).strip()
    return Path(common).parent / ".scratch" / "keviq_test.lock"


@contextmanager
def locked_test_database(path: Path | None = None):
    path = path or lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    owner = {"owner": "test-suite", "pid": os.getpid(), "token": token,
             "started_at": datetime.now(UTC).isoformat()}  # fmt: skip
    try:
        handle = path.open("x")  # atomic claim; existence is also visible to other operators
    except FileExistsError:
        try:
            current = json.loads(path.read_text())
        except (ValueError, OSError):
            current = {}
        inherited = os.environ.get(TOKEN_ENV)
        if inherited and inherited == current.get("token"):
            yield current  # a child fixture of the command holding the suite-wide lock
            return
        raise LockBusy(
            "keviq_test is busy: .scratch/keviq_test.lock already exists; do not overwrite it"
        ) from None
    with handle:
        handle.write(json.dumps(owner) + "\n")
    try:
        yield owner
    finally:
        # Never remove another run's replacement file, even on an exception or interrupted command.
        try:
            if json.loads(path.read_text()).get("token") == token:
                path.unlink()
        except (ValueError, OSError):
            pass


def main(argv=None) -> int:
    command = list(sys.argv[1:] if argv is None else argv)
    if command[:1] == ["--"]:
        command.pop(0)
    if not command:
        raise SystemExit("usage: python -m tests.db_lock -- COMMAND ...")
    url = testing_database_url()
    if url and not is_test_database(url):
        raise SystemExit("refusing a database whose name does not end in _test")
    try:
        with locked_test_database() as owner:
            env = dict(os.environ, MODEL_API_KEY="", **{TOKEN_ENV: owner["token"]})
            if url:
                env["DATABASE_URL"] = url
            return subprocess.run(command, env=env).returncode
    except LockBusy as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
