"""run.json records the tree a C5 run ran on: HEAD, whether tracked files differ, and the merged pull requests, read
from first-parent merge subjects in either form GitHub writes ("#N: title" and the default "#N from owner/branch")."""

import subprocess

from measure import c5

SUBJECTS = "\n".join([
    "Merge pull request #81: Relabel cancel and complaint comments",
    "Merge branch 'main' into feature",
    "Merge pull request #10 from owner/orders-ask-variant",
    "Merge pull request #7: Add the order rules",
])  # fmt: skip


def fake(status: str):
    def run(args, **kwargs):
        out = {"log": SUBJECTS + "\n", "rev-parse": "abc123\n", "status": status}[args[1]]
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    return run


def test_git_state_reads_both_merge_subject_forms(monkeypatch):
    monkeypatch.setattr(c5.subprocess, "run", fake(""))
    assert c5.git_state() == {"head": "abc123", "dirty": False, "merged_prs": [81, 10, 7]}


def test_git_state_marks_a_tree_with_changed_tracked_files_dirty(monkeypatch):
    monkeypatch.setattr(c5.subprocess, "run", fake(" M measure/c5_shop.py\n"))
    assert c5.git_state()["dirty"] is True
