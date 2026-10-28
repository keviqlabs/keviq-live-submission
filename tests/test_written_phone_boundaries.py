"""Maximal phone runs and full-input coverage; synthetic numbers only."""

import pytest

from worker.brain.shield import scan


@pytest.mark.parametrize(
    "text",
    [
        "gọi 0,9,0,0,0,0,0,0,1,3",
        "gọi 84,9000,0001,3",
        "x" * 10000 + " gọi không chín không không không không không không một ba",
        " ".join(["không"] * 10000) + " kết thúc 0900000013",
    ],
)
def test_commas_and_a_phone_after_a_long_nonphone_run_are_detected(text):
    assert "phone_number" in scan(text)


@pytest.mark.parametrize(
    "text",
    [
        "+0900000013",
        "0" * 10000 + "0900000013",
        "0,0,9,0,0,0,0,0,0,1,3",
        "khôngchínkhôngkhôngkhôngkhôngkhôngkhôngmộtba",
        "mã 0900000013X và C0900000013",
        "٠٩٠٠٠٠٠٠١٣",
    ],
)
def test_invalid_maximal_runs_and_identifier_fragments_are_not_phones(text):
    assert "phone_number" not in scan(text)
