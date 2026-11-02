import asyncio
import sys

import pytest

from worker.cohost.playback import LocalSpeechPlayer


@pytest.mark.parametrize("during_start", [False, True])
async def test_cancellation_reaps_owned_process_without_real_audio(monkeypatch, during_start):
    original = asyncio.create_subprocess_exec
    processes = []
    spawned = asyncio.Event()
    release_start = asyncio.Event()

    async def start(*args, **kwargs):
        assert args[:2] == ("/usr/bin/say", "-v")
        process = await original(sys.executable, "-c", "import time; time.sleep(30)", **kwargs)
        processes.append(process)
        spawned.set()
        if during_start:
            await release_start.wait()
        return process

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    task = asyncio.create_task(LocalSpeechPlayer().speak("Synthetic speech"))
    await spawned.wait()
    task.cancel()
    release_start.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert len(processes) == 1
    assert processes[0].returncode is not None
