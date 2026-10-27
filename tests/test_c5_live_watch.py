"""The live runner stops its watch between polls: a poll in flight is never cancelled."""

import asyncio
from contextlib import asynccontextmanager

from measure import c5_live
from measure.c5_shop import Feed


class Http:
    """One events stream that holds until `gate` is set, like a poll waiting on the app."""

    def __init__(self):
        self.gate, self.entered = asyncio.Event(), asyncio.Event()
        self.polls, self.finished, self.cancelled = 0, 0, False

    @asynccontextmanager
    async def stream(self, method, url, params=None):
        self.polls += 1
        yield self

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        self.entered.set()
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.finished += 1
        for line in ():
            yield line


class Shop:
    def see(self, ev):
        pass


def test_stopping_the_watch_waits_for_the_poll_in_flight():
    async def scenario():
        http = Http()
        watch = c5_live.Watch(http, "run", Feed(), Shop())
        watching = asyncio.create_task(watch.run())
        await asyncio.wait_for(http.entered.wait(), 1)
        halting = asyncio.create_task(watch.halt(watching))
        await asyncio.sleep(0.1)
        held = (halting.done(), http.cancelled)
        http.gate.set()
        await asyncio.wait_for(halting, 2)
        return held, http, watching

    held, http, watching = asyncio.run(scenario())
    assert held == (False, False)  # still waiting on the poll, which was not cancelled
    assert (http.polls, http.finished, http.cancelled) == (1, 1, False)
    assert watching.done() and not watching.cancelled()
