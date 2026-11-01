"""Which question about the item on screen gets an answer, and when.

Comments that ask the same thing about the same card (same kind, same size and colour) within window_ms form one group and get
one answer, written when the group is due, from the card state of that moment. Spoken answers keep cooldown_ms
apart; silent decisions do not count. A group that cannot be answered within max_wait_ms of its first comment is
dropped as too_late instead of being said late. When the item on screen changes, every pending group is dropped
(item_changed), and a question in the first guard_ms after a change stays unanswered (just_switched): it may be
about the item before. Standard library only.
"""

from dataclasses import dataclass, field

from .facts import Board
from .questions import Question, parse
from .reply import compose


@dataclass(frozen=True)
class Decision:
    ts_ms: int
    comment_ids: tuple[str, ...]
    kind: str
    size: str | None
    action: str  # answer | ask | defer | silent
    text: str
    card_id: str | None
    version: int | None
    reason: str
    asked_ms: int


@dataclass
class _Group:
    question: Question
    card_id: str
    first_ms: int
    ids: list[str] = field(default_factory=list)


class Selector:
    def __init__(self, window_ms=4000, cooldown_ms=3000, guard_ms=3000, max_wait_ms=10000):
        self.window_ms, self.cooldown_ms, self.guard_ms, self.max_wait_ms = (
            window_ms,
            cooldown_ms,
            guard_ms,
            max_wait_ms,
        )
        self.board = Board()
        self._groups: dict[tuple, _Group] = {}
        self._last_spoken_ms: int | None = None

    @property
    def pending(self) -> int:
        return len(self._groups)

    def _silent(self, ts_ms, ids, q, reason, asked_ms, facts=None):
        card_id, version = (facts.card_id, facts.version) if facts else (None, None)
        return Decision(ts_ms, tuple(ids), q.kind, q.size, "silent", "", card_id, version, reason, asked_ms)

    def card(self, payload: dict, ts_ms: int) -> list[Decision]:
        """Take a card first, so a group due at this very moment is answered from the newest state."""
        out = []
        if self.board.ingest(payload, ts_ms):
            for g in sorted(self._groups.values(), key=lambda g: g.first_ms):
                out.append(self._silent(ts_ms, g.ids, g.question, "item_changed", g.first_ms))
            self._groups.clear()
        return out + self.due(ts_ms)

    def comment(self, comment_id: str, ts_ms: int, text: str, label: str | None = None) -> list[Decision]:
        out = self.due(ts_ms)
        q = parse(text, label)
        if q is None:
            return out
        facts = self.board.current()
        if facts is None:
            return [*out, self._silent(ts_ms, [comment_id], q, "no_card", ts_ms)]
        switched = self.board.switched_ms
        if switched is not None and ts_ms - switched < self.guard_ms:
            return [*out, self._silent(ts_ms, [comment_id], q, "just_switched", ts_ms, facts)]
        key = (facts.card_id, q.kind, q.size, q.color)
        self._groups.setdefault(key, _Group(q, facts.card_id, ts_ms)).ids.append(comment_id)
        return out

    def due(self, now_ms: int) -> list[Decision]:
        out = []
        for key, g in sorted(self._groups.items(), key=lambda kv: kv[1].first_ms):
            if now_ms < g.first_ms + self.window_ms:
                continue
            if now_ms - g.first_ms > self.max_wait_ms:
                out.append(self._silent(now_ms, g.ids, g.question, "too_late", g.first_ms))
                del self._groups[key]
                continue
            a = compose(g.question, self.board.current())
            if a.action != "silent":
                if self._last_spoken_ms is not None and now_ms - self._last_spoken_ms < self.cooldown_ms:
                    continue
                self._last_spoken_ms = now_ms
            q = g.question
            out.append(Decision(now_ms, tuple(g.ids), q.kind, q.size, a.action, a.text, a.card_id, a.version,
                                a.reason, g.first_ms))  # fmt: skip
            del self._groups[key]
        return out


def replay(events, selector: Selector | None = None, tick_ms: int = 250) -> list[Decision]:
    """Feed card and comment events in time order, checking due groups every tick_ms; flush at the end."""
    s = selector or Selector()
    out: list[Decision] = []
    t = None
    for ev in sorted(events, key=lambda e: e["t_ms"]):
        if t is not None:
            t = (t // tick_ms + 1) * tick_ms
            while t < ev["t_ms"]:
                out += s.due(t)
                t += tick_ms
        t = ev["t_ms"]
        if ev["type"] == "card":
            out += s.card(ev["payload"], t)
        elif ev["type"] == "comment":
            out += s.comment(ev["comment_id"], t, ev["text"], ev.get("label"))
    while t is not None and s.pending:
        t = (t // tick_ms + 1) * tick_ms
        out += s.due(t)
    return out
