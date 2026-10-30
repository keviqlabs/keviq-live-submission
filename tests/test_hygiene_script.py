"""The repository hygiene guard (scripts/hygiene.sh) on small throw-away git repositories.

Exit codes are the contract: 0 clean, 1 findings, 2 usage or nothing to compare with.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# HYGIENE_SCRIPT points the suite at another copy of the guard (to show red on an older one).
SCRIPT = Path(os.environ.get("HYGIENE_SCRIPT") or ROOT / "scripts" / "hygiene.sh")
PRIVATE = "Someone <1+someone@users.noreply.github.com>"
PERSONAL = "Someone <someone@example.com>"
ZEROS = "0" * 40


def git(repo: Path, *args: str, identity: str = PRIVATE) -> str:
    name, email = identity[:-1].split(" <")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def commit(repo: Path, message: str, files: dict[str, bytes], identity: str = PRIVATE) -> str:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message, identity=identity)
    return git(repo, "rev-parse", "HEAD")


def guard(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit(r, "Add a clean file", {"notes.txt": b"plain text\n"})
    return r


def test_clean_range_exits_zero(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, "Add more plain text", {"more.txt": b"nothing to see\n"})
    result = guard(repo, base, head)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checked 1 commit(s)" in result.stdout
    assert "clean" in result.stdout


def test_forbidden_word_in_message_exits_one(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, "Tidy wording\n\nCo-authored-by: Tool <tool@example.com>", {"a.txt": b"x\n"})
    result = guard(repo, base, head)
    assert result.returncode == 1
    assert "forbidden word in commit message" in result.stdout


def test_personal_address_exits_one(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, "Plain change", {"b.txt": b"x\n"}, identity=PERSONAL)
    result = guard(repo, base, head)
    assert result.returncode == 1
    assert "personal address" in result.stdout


def test_unresolvable_base_exits_two_not_zero(repo: Path) -> None:
    """D1: a base that does not resolve used to print "checked  commit(s)" and exit 0."""
    head = git(repo, "rev-parse", "HEAD")
    for bad in (ZEROS, "no-such-ref"):
        result = guard(repo, bad, head)
        assert result.returncode == 2, (bad, result.stdout, result.stderr)
        assert "is not a commit" in result.stderr
        assert "checked" not in result.stdout


def test_unresolvable_head_exits_two(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    result = guard(repo, base, "no-such-head")
    assert result.returncode == 2
    assert "checked" not in result.stdout


def test_unknown_flag_and_bad_arity_exit_two(repo: Path) -> None:
    assert guard(repo, "--help").returncode == 2
    assert guard(repo, "--message").returncode == 2
    assert guard(repo, "--message", "missing.txt").returncode == 2
    assert guard(repo, "a", "b", "c").returncode == 2


def test_no_main_to_compare_with_exits_two(tmp_path: Path) -> None:
    """S2: with neither origin/main nor main, the guard refuses rather than passing silently."""
    r = tmp_path / "orphan"
    r.mkdir()
    git(r, "init", "-q", "-b", "topic")
    commit(r, "Only commit", {"x.txt": b"x\n"})
    result = guard(r)
    assert result.returncode == 2
    assert "fetch main first" in result.stderr


def test_message_mode_scans_hash_lines_but_not_the_template(repo: Path, tmp_path: Path) -> None:
    """D2: a '#' line the author wrote is part of the message; git's editor template is not."""
    authored = tmp_path / "authored.txt"
    authored.write_text("Tidy wording\n\n# Generated with a tool\n")
    result = guard(repo, "--message", str(authored))
    assert result.returncode == 1, result.stdout
    assert "Generated with" in result.stdout

    template = tmp_path / "template.txt"
    template.write_text(
        "Tidy wording\n"
        "# Please enter the commit message for your changes. Lines starting\n"
        "# with '#' will be ignored, and an empty message aborts the commit.\n"
        "#\n"
        "# On branch claude/some-branch\n"
    )
    assert guard(repo, "--message", str(template)).returncode == 0

    scissors = tmp_path / "scissors.txt"
    scissors.write_text("Tidy wording\n# ------------------------ >8 ------------------------\n# claude\n")
    assert guard(repo, "--message", str(scissors)).returncode == 0


def test_non_ascii_path_is_scanned(repo: Path) -> None:
    """D3: git quotes non-ASCII paths in `ls-files`; the tree scan must still read the file."""
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, "Add a note", {"docs/ghi chú.md": b"Generated with a tool\n"})
    result = guard(repo, base, head)
    assert result.returncode == 1
    assert "forbidden word in tracked files" in result.stdout
    assert "docs/ghi chú.md" in result.stdout


def test_file_name_is_scanned(repo: Path) -> None:
    """S1: a forbidden word in a path is a finding even when the content is clean."""
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, "Add evidence", {"docs/claude-notes.md": b"plain\n"})
    result = guard(repo, base, head)
    assert result.returncode == 1
    assert "forbidden word in file name" in result.stdout
    assert "docs/claude-notes.md" in result.stdout


def test_label_words_in_file_content_are_caught_on_every_platform(repo: Path) -> None:
    """N1: the label alternatives used `\\b`, which `git grep` on macOS does not know, so a desk
    label or a decision code in a tracked file passed there while the Linux workflow caught it."""
    base = git(repo, "rev-parse", "HEAD")
    head = commit(
        repo,
        "Add a log",
        {"docs/log.md": b"Seat: HQ2 signs off.\nSee DEC-031.\nAgent 7 ran it.\nAstra asked.\n"},
    )
    result = guard(repo, base, head)
    assert result.returncode == 1, result.stdout
    assert "forbidden word in tracked files" in result.stdout
    for line in ("docs/log.md:1:", "docs/log.md:2:", "docs/log.md:3:", "docs/log.md:4:"):
        assert line in result.stdout, result.stdout


def test_label_words_inside_other_words_are_not_findings(repo: Path) -> None:
    """The spelled-out boundary must still be a boundary: substrings of ordinary words are clean."""
    base = git(repo, "rev-parse", "HEAD")
    head = commit(
        repo,
        "Add prose",
        {"docs/prose.md": b"The CHQ2 form, the DECK-1 and agent 7x in Astral light, Agent 7a.\n"},
    )
    result = guard(repo, base, head)
    assert result.returncode == 0, result.stdout


def test_all_mode_checks_every_commit_reachable_from_head(repo: Path) -> None:
    """N3: with no usable base (new or rewritten main), nothing may pass unchecked; `--all` walks
    the whole history instead of comparing with the parent of the tip."""
    commit(repo, "Slip\n\nCo-authored-by: Tool <tool@example.com>", {"old.txt": b"x\n"})
    head = commit(repo, "Clean tip", {"new.txt": b"y\n"})
    # The parent-of-tip fallback would pass: the tip alone is clean.
    assert guard(repo, f"{head}~1", head).returncode == 0
    result = guard(repo, "--all", head)
    assert result.returncode == 1, result.stdout
    assert "forbidden word in commit message" in result.stdout
    assert "checked 3 commit(s) reachable from" in result.stdout
    # Without an explicit head it walks from HEAD; a clean history is clean.
    clean = repo.parent / "clean"
    clean.mkdir()
    git(clean, "init", "-q", "-b", "main")
    commit(clean, "Only commit", {"x.txt": b"x\n"})
    result = guard(clean, "--all")
    assert result.returncode == 0, result.stdout
    assert "checked 1 commit(s) reachable from" in result.stdout
    assert guard(repo, "--all", "no-such-head").returncode == 2
    assert guard(repo, "--all", "a", "b").returncode == 2


def test_binary_content_is_not_scanned(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, "Add a blob", {"blob.bin": b"\x00\x01Generated with\x00\xff"})
    result = guard(repo, base, head)
    assert result.returncode == 0, result.stdout


def test_scans_the_tree_at_head_not_the_working_copy(repo: Path) -> None:
    """The documented contract is "tracked files at head"; a dirty working copy must not matter."""
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, "Add text", {"c.txt": b"plain\n"})
    (repo / "c.txt").write_text("Generated with a tool\n")  # uncommitted
    assert guard(repo, base, head).returncode == 0
    (repo / "c.txt").write_text("plain\n")
    commit(repo, "Slip", {"c.txt": b"Generated with a tool\n"})
    assert guard(repo, base, head).returncode == 0  # head still points at the clean commit
    assert guard(repo, base, "HEAD").returncode == 1


def test_tool_prefixed_branch_warns(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, "Plain", {"d.txt": b"plain\n"})
    result = guard(repo, base, head, env={"GITHUB_HEAD_REF": "claude/some-branch"})
    assert result.returncode == 0
    assert "::warning::" in result.stdout
