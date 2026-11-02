"""python -m worker [--base-url URL] [--from-start] [--session ID]"""

import argparse
import asyncio
import logging

from answers import handlers as answers
from orders import handlers as orders
from worker import brain, media, vision, voice
from worker.runner import Runner


def handlers() -> dict:
    merged: dict[str, list] = {}
    for module in (media, vision, voice, brain, orders, answers):
        for etype, fns in module.HANDLERS.items():
            merged.setdefault(etype, []).extend(fns)
    return merged


def main(argv=None):
    p = argparse.ArgumentParser(prog="worker")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--from-start", action="store_true", help="process history from seq 0 instead of the head")
    p.add_argument("--session", help="only this session")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(Runner(args.base_url, handlers()).run(0 if args.from_start else None, args.session))


if __name__ == "__main__":
    main()
