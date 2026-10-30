import asyncio
from dataclasses import replace

import pytest

from worker.cohost import CardSnapshot, Cohost
from worker.cohost.controller import DISCLOSURE


class Clock:
    now = 10.0

    def __call__(self):
        return self.now


class Player:
    def __init__(self, hold=False):
        self.texts = []
        self.hold = hold
        self.started = asyncio.Event()
        self.released = asyncio.Event()
        self.stopped = asyncio.Event()
        self.active = 0
        self.peak = 0

    async def speak(self, text):
        self.texts.append(text)
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.set()
        try:
            if self.hold:
                await self.released.wait()
        finally:
            self.active -= 1
            self.stopped.set()


@pytest.fixture
def card():
    return CardSnapshot("sample-card", 1, "Giày mẫu", 250000, True, ("38", "39"))


@pytest.fixture
async def setup(monkeypatch, card):
    monkeypatch.setenv("COHOST_ENABLED", "1")
    clock, player = Clock(), Player()
    cohost = Cohost(player, mode="replay", clock=clock)
    await cohost.microphone_ready()
    assert await cohost.enable()
    clock.now += 1
    await cohost.replace_card(1, card)
    yield cohost, player, clock
    await cohost.disable()


@pytest.mark.parametrize("flag", [None, "0", "true", "yes", ""])
async def test_flag_defaults_off_and_requires_exact_opt_in(monkeypatch, card, flag):
    monkeypatch.delenv("COHOST_ENABLED", raising=False)
    if flag is not None:
        monkeypatch.setenv("COHOST_ENABLED", flag)
    player = Player()
    c = Cohost(player, mode="demo")
    await c.microphone_ready()
    assert not await c.enable()
    await c.replace_card(1, card)
    assert not await c.request_turn()
    assert not player.texts


def test_live_mode_is_rejected():
    with pytest.raises(ValueError, match="replay or demo"):
        Cohost(Player(), mode="live")


async def test_explicit_enable_requires_microphone(monkeypatch):
    monkeypatch.setenv("COHOST_ENABLED", "1")
    c = Cohost(Player(), mode="demo")
    assert not await c.enable()
    await c.microphone_ready()
    assert not c.enabled
    assert await c.enable()
    await c.disable()


async def test_disclosure_precedes_exact_approved_template(setup, card):
    c, p, _ = setup
    assert await c.request_turn()
    await c.wait_idle()
    assert p.texts == [DISCLOSURE, "Giày mẫu, giá 250,000 đồng. Size còn: 38, 39."]
    assert not await c.request_turn()
    assert p.peak == 1


async def test_unknown_availability_is_not_claimed(setup, card):
    c, p, _ = setup
    await c.replace_card(2, replace(card, version=2, available_sizes=None))
    assert await c.request_turn()
    await c.wait_idle()
    assert p.texts == [DISCLOSURE, "Giày mẫu, giá 250,000 đồng."]


async def test_unapproved_and_revoked_card_cannot_speak(setup, card):
    c, p, _ = setup
    assert not await c.replace_card(2, replace(card, version=2, approved=False))
    assert not await c.request_turn()
    # A newer envelope must not resurrect an older approved card version.
    assert not await c.replace_card(3, card)
    assert not await c.request_turn()
    await c.replace_card(4, replace(card, version=3))
    await c.replace_card(5, None)
    assert not await c.request_turn()
    assert p.texts == []


async def test_old_sequence_and_conflicting_version_cannot_replace_current(setup, card):
    c, p, _ = setup
    assert await c.replace_card(3, replace(card, version=2, price_vnd=300000))
    assert not await c.replace_card(2, card)
    assert not await c.replace_card(4, replace(card, version=2, price_vnd=1))
    assert not await c.request_turn()
    assert not p.texts


async def test_snapshot_expires_and_duplicate_cannot_extend_it(setup, card):
    c, p, clock = setup
    clock.now += 5
    assert not await c.replace_card(2, card)
    assert not await c.request_turn()
    assert not p.texts


async def test_host_barge_in_cancels_output_and_requires_new_turn(setup):
    c, p, clock = setup
    p.hold = True
    assert await c.request_turn()
    await p.started.wait()
    await c.host_activity(True)
    assert p.stopped.is_set() and p.active == 0
    assert c.last_stop_ms == 0  # deterministic clock; no hardware latency claim
    assert not await c.request_turn()
    await c.host_activity(False)
    assert not await c.request_turn()
    clock.now += 1
    p.hold = False
    assert await c.request_turn()
    await c.wait_idle()
    # The interrupted announcement must be completed before any product sentence.
    assert p.texts[:2] == [DISCLOSURE, DISCLOSURE]
    assert len(p.texts) == 3


async def test_switch_cancels_old_product_and_cannot_leak_stale_text(setup, card):
    c, p, clock = setup
    await c.request_turn()
    await c.wait_idle()
    clock.now += 21
    await c.replace_card(2, replace(card, version=2))
    p.hold = True
    p.started.clear()
    await c.request_turn()
    await p.started.wait()
    await c.replace_card(3, replace(card, card_id="next-card", name="Mẫu khác"))
    assert p.active == 0
    assert p.peak == 1
    assert p.texts[-1].startswith("Giày mẫu")
    assert not await c.request_turn()  # cooldown also applies to interrupted product speech


@pytest.mark.parametrize("fault", ["device_changed", "microphone_lost", "reconnected", "disable"])
async def test_lifecycle_fault_latches_off_even_after_ready_and_enable(setup, card, fault):
    c, p, clock = setup
    p.hold = True
    await c.request_turn()
    await p.started.wait()
    await getattr(c, fault)()
    assert p.active == 0
    assert c.latched_off and not c.enabled
    await c.microphone_ready()
    await c.host_activity(False)
    clock.now += 1
    assert not await c.enable()
    assert not await c.replace_card(2, replace(card, version=2))
    assert not await c.request_turn()


async def test_only_one_session_can_own_output(setup, card):
    c, p, clock = setup
    other_player = Player()
    other = Cohost(other_player, mode="demo", clock=clock)
    await other.microphone_ready()
    await other.enable()
    clock.now += 1
    await other.replace_card(1, card)
    p.hold = True
    try:
        await c.request_turn()
        await p.started.wait()
        assert not await c.request_turn()
        assert not await other.request_turn()
        await c.host_activity(True)
        assert await other.request_turn()
        await other.wait_idle()
        assert p.active == 0 and other_player.peak == 1
    finally:
        await other.disable()


async def test_cancel_before_task_starts_releases_output(setup, card):
    c, p, _ = setup
    assert await c.request_turn()
    await c.replace_card(2, replace(card, version=2))
    assert not p.texts
    assert await c.request_turn()
    await c.wait_idle()
    assert p.texts[0] == DISCLOSURE


async def test_playback_failure_latches_off(setup):
    c, p, _ = setup

    async def broken(text):
        raise RuntimeError("output failed")

    p.speak = broken
    assert await c.request_turn()
    with pytest.raises(RuntimeError):
        await c.wait_idle()
    assert c.latched_off and not c.enabled
    assert c.error == "playback_failed"


async def test_watchdog_cancels_hung_player_and_latches_off(setup, monkeypatch):
    from worker.cohost import controller

    c, p, _ = setup
    p.hold = True
    monkeypatch.setattr(controller, "PLAYBACK_TIMEOUT_S", 0.02)
    assert await c.request_turn()
    with pytest.raises(TimeoutError):
        await c.wait_idle()
    assert p.active == 0 and p.stopped.is_set()
    assert c.latched_off and not c.enabled


async def test_unacknowledged_stop_latches_off_and_keeps_output_lease(setup, card):
    c, p, clock = setup
    c.last_stop_ms = 123.0  # a prior acknowledgement must not survive a failed stop
    cancelled, release = asyncio.Event(), asyncio.Event()

    async def stubborn(text):
        p.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    p.speak = stubborn
    other = Cohost(Player(), mode="demo", clock=clock)
    try:
        await c.request_turn()
        await p.started.wait()
        await c.host_activity(True)
        assert cancelled.is_set()
        assert c.error == "playback_did_not_stop"
        assert c.last_stop_ms is None
        assert c.latched_off and not c.enabled
        await other.microphone_ready()
        await other.enable()
        clock.now += 1
        await other.replace_card(1, card)
        assert not await other.request_turn()
    finally:
        release.set()
        await c.wait_idle()
        await other.disable()


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "Name\nnew sentence"),
        ("name", "<speak>text</speak>"),
        ("name", "x" * 49),
        ("price_vnd", True),
        ("price_vnd", -1),
        ("approved", "true"),
        ("available_sizes", ["38"]),
        ("version", True),
    ],
)
def test_invalid_or_mutable_snapshot_is_rejected(card, field, value):
    with pytest.raises(ValueError):
        replace(card, **{field: value})
