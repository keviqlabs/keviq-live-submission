"""measure/c5_live Watch.barrier settles only on two ok checks with the same head, a newer progress line and two polls
of the session between them (#41): each part of that rule on its own, on a clock of its own (no real waits)."""

import asyncio

from measure import c5_live


class Clock:
    """time.monotonic and asyncio.sleep for c5_live: a sleep moves the clock, nothing waits."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s


class Scripted(c5_live.Watch):
    """check() gives the scripted looks in turn, then the last one again, with the polls each says."""

    def __init__(self, looks: list[dict]):
        super().__init__(None, "run", None, None)
        self.looks, self.last = iter(looks), None

    def check(self, worker, tasks: list[set]) -> dict:
        self.last = next(self.looks, self.last)
        self.polls = self.last["polls"]
        return dict(self.last)


def barrier(monkeypatch, looks: list[dict], timeout_s: float = 5.0) -> dict:
    watch, clock = Scripted(looks), Clock()  # the watch's Events made before c5_live's asyncio is the clock
    monkeypatch.setattr(c5_live, "time", clock)
    monkeypatch.setattr(c5_live, "asyncio", clock)
    return asyncio.run(watch.barrier(None, [set()], timeout_s=timeout_s))


def looks(n: int = 20, head=lambda i: 9, line=lambda i: i, polls=lambda i: i) -> list[dict]:
    return [{"ok": True, "head_seq": head(i), "n": line(i), "polls": polls(i)} for i in range(n)]


def test_everything_advancing_at_one_head_settles_on_the_third_check(monkeypatch):
    row = barrier(monkeypatch, looks())
    assert (row["status"], row["waited_ms"], row["head_seq"]) == ("SETTLED", 1000, 9)


def test_no_newer_progress_line_never_settles(monkeypatch):
    """A worker that stopped saying anything: its last line ok, the session polled on and on."""
    assert barrier(monkeypatch, looks(line=lambda i: 4))["status"] == "INCOMPLETE"


def test_no_new_poll_of_the_session_never_settles(monkeypatch):
    """The worker's lines come, but nobody has read the session since: a consequence it posted is not seen yet."""
    assert barrier(monkeypatch, looks(polls=lambda i: 0))["status"] == "INCOMPLETE"


def test_a_head_that_moves_on_every_check_never_settles(monkeypatch):
    """The worker keeps pace with a session that keeps growing: each check is at a new head. Lines and polls both
    advance enough between two checks, so only the same-head part of the rule keeps it from settling."""
    assert barrier(monkeypatch, looks(head=lambda i: 9 + i, polls=lambda i: 2 * i))["status"] == "INCOMPLETE"


def test_a_check_that_is_not_ok_starts_the_two_checks_again(monkeypatch):
    rows = looks()
    rows[2] = {**rows[2], "ok": False}
    row = barrier(monkeypatch, rows)  # first again at the 4th check, settled at the 6th
    assert (row["status"], row["waited_ms"]) == ("SETTLED", 2500)
