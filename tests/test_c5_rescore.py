"""measure.c5_rescore: the run file a rescore writes (the reading and scoring are the dry run's own)."""

import pytest

from measure import c5
from measure.c5_rescore import rescore_run_file


def run_file(**over) -> dict:
    return {"run": "c5-dry-x", "session_id": "c5-dry-x", "config": {"catalog_path": "data/comments/c5/catalog.json"},
            "loops": [{"k": 1}]} | over  # fmt: skip


def test_a_run_becomes_its_rescore():
    out = rescore_run_file(run_file(), "c5-dry-x.json")
    assert (out["run"], out["rescored_from"], out["session_id"]) == (
        "c5-dry-x-rescore",
        "c5-dry-x.json",
        "c5-dry-x",
    )


def test_a_committed_rescore_keeps_its_name_and_keys():
    first = rescore_run_file(run_file(), "c5-dry-x.json")
    again = rescore_run_file(first, "c5-dry-x-rescore.json")
    assert again == first and list(again) == list(first)


def test_an_absolute_catalog_path_is_recorded_relative_to_the_checkout():
    cfg = {"catalog_path": str(c5.ROOT / "data/comments/c5/catalog.json")}
    assert (
        rescore_run_file(run_file(config=cfg), "r.json")["config"]["catalog_path"]
        == "data/comments/c5/catalog.json"
    )


def test_rescore_refuses_live_flags():
    with pytest.raises(SystemExit):
        c5.main(["--rescore", "r.json", "--paid"])
