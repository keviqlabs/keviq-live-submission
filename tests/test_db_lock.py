import json
import os
import subprocess
import sys

import pytest

from tests.db_lock import TOKEN_ENV, LockBusy, locked_test_database


def test_another_process_cannot_claim_or_remove_the_test_database_lock(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    path = tmp_path / "keviq_test.lock"
    with locked_test_database(path) as owner:
        command = [sys.executable, "-c", "from pathlib import Path; from tests.db_lock import locked_test_database; "
                   "import sys;\nwith locked_test_database(Path(sys.argv[1])): pass", str(path)]  # fmt: skip
        env = dict(os.environ)
        env.pop(TOKEN_ENV, None)
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        assert result.returncode != 0 and "already exists" in result.stderr
        assert json.loads(path.read_text()) == owner
        assert "token" not in owner  # a field named token trips the secret scan on the live lock file
    assert not path.exists()


def test_a_child_fixture_keeps_its_parent_commands_lock(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    path = tmp_path / "keviq_test.lock"
    with locked_test_database(path) as owner:
        monkeypatch.setenv(TOKEN_ENV, owner["lock_id"])
        with locked_test_database(path) as child:
            assert child == owner
        assert json.loads(path.read_text()) == owner
    assert not path.exists()


def test_a_failure_releases_only_its_own_lock_and_leaves_an_existing_foreign_lock(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    path = tmp_path / "keviq_test.lock"
    with pytest.raises(ValueError), locked_test_database(path):
        raise ValueError("synthetic failure")
    assert not path.exists()
    path.write_text("another test run\n")
    with pytest.raises(LockBusy), locked_test_database(path):
        pass
    assert path.read_text() == "another test run\n"
