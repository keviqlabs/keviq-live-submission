"""The README's sentence on what was made before and during the event is said word for word in the video, so the
copy in docs/video-script.md must stay the same as the README's."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def disclosure() -> str:
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    i = lines.index("## Built during the hackathon")
    return next(line for line in lines[i + 1 :] if line.strip())


def test_readme_disclosure_names_the_event_start():
    start = "Everything in this repository was made during the event, from 26 October 2026 23:24 ICT;"
    assert disclosure().startswith(start)


def test_video_script_says_the_readme_sentence_word_for_word():
    script = (ROOT / "docs" / "video-script.md").read_text(encoding="utf-8").splitlines()
    assert f"> {disclosure()}" in script
