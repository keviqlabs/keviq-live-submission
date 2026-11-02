"""Local output adapters; neither adapter reads a microphone or sends network requests."""

import asyncio
import json
import sys


class TranscriptPlayer:
    """A silent demonstration adapter. Its output is not an acoustic latency measurement."""

    async def speak(self, text: str) -> None:
        print(json.dumps({"type": "cohost_transcript", "text": text}, ensure_ascii=False), flush=True)


class LocalSpeechPlayer:
    """Use an already installed macOS voice; cancel only the owned speech process.

    There is no fallback or voice download. Device lifecycle and host activity still need
    explicit input from a trusted local adapter; this player cannot detect those signals.
    """

    def __init__(self, voice: str = "Linh"):
        if sys.platform != "darwin":
            raise RuntimeError("local speech requires macOS")
        self.voice = voice

    async def speak(self, text: str) -> None:
        # Shield process creation so cancellation cannot leave a spawned child unowned.
        starting = asyncio.create_task(
            asyncio.create_subprocess_exec(
                "/usr/bin/say",
                "-v",
                self.voice,
                "-r",
                "180",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        )
        process = None
        try:
            process = await asyncio.shield(starting)
            await process.communicate(text.encode("utf-8"))
            if process.returncode != 0:
                raise RuntimeError("local speech failed")
        finally:
            if process is None:
                process = await starting
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=0.3)
                except TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
