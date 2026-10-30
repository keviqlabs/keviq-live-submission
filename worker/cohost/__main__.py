"""Operator-owned JSON-lines replay input, with silent output unless --audio is explicit."""

import argparse
import asyncio
import json
import sys

from .controller import CardSnapshot, Cohost
from .playback import LocalSpeechPlayer, TranscriptPlayer


async def dispatch(cohost: Cohost, event: dict) -> None:
    kind = event["type"]
    if kind == "microphone_ready":
        await cohost.microphone_ready()
    elif kind == "enable":
        await cohost.enable()
    elif kind == "card":
        raw = event["card"]
        if raw is not None:
            raw = dict(raw)
            if raw.get("available_sizes") is not None:
                if not isinstance(raw["available_sizes"], list):
                    raise ValueError("size availability must be a list")
                raw["available_sizes"] = tuple(raw["available_sizes"])
            raw = CardSnapshot(**raw)
        await cohost.replace_card(event["sequence"], raw)
    elif kind == "host":
        await cohost.host_activity(event["speaking"])
    elif kind == "turn":
        await cohost.request_turn()
    elif kind == "wait":
        seconds = event["seconds"]
        if type(seconds) not in {int, float} or not 0 <= seconds <= 30:
            raise ValueError("wait must be between zero and thirty seconds")
        await asyncio.sleep(seconds)
    elif kind in {"device_changed", "microphone_lost", "reconnected"}:
        await getattr(cohost, kind)()
    elif kind == "off":
        await cohost.disable()
    else:
        raise ValueError("unknown replay event")


async def run(mode: str, audio: bool) -> None:
    cohost = Cohost(LocalSpeechPlayer() if audio else TranscriptPlayer(), mode=mode)
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            await dispatch(cohost, json.loads(line))
        await cohost.wait_idle()
    finally:
        await cohost.disable("input_closed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["replay", "demo"], required=True)
    parser.add_argument(
        "--audio", action="store_true", help="speak locally; requires a supervised headphones demo"
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args.mode, args.audio))
    except (ValueError, KeyError, TypeError, RuntimeError):
        # Do not echo rejected input, which could contain private card data.
        print("cohost stopped: invalid input or unavailable playback", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
