"""python -m measure.c5_proc worker|worker.media [args]: `python -m <module> [args]` after one line of what this
process really loaded (ruling 01:39, item 2: the app's env proves nothing about the worker's or media's): the catalog
file and its sha256, the catalog the card and labels brains hold, the cap and warning its Meter takes, the vision
effort, the git sha. On SIGTERM it prints the card brain's `Session.waits` of each session it holds and exits: the
loop-end snapshot, taken before the restart (ruling 419). The worker also says, in "progress" lines, how far it has
finished the stream (measure/c5_progress), for the loop-end barrier; the last one again on SIGTERM. Its lines start
with "C5PROC " and hold no secret (the model key only as set or not).

With C5_OFFLINE=<loop k> and the model key empty, the worker gets the stored-output stand-ins of measure/c5_offline
(gold labels, stored cut-outs) before it starts, and says so in an "offline" line. With a key set it refuses."""

import hashlib
import json
import os
import runpy
import signal
import sys

TAG = "C5PROC "
MODULES = ("worker", "worker.media")
PROGRESS = None  # the worker's measure/c5_progress count


def say(kind: str, **fields) -> None:
    sys.stdout.write(
        TAG + json.dumps({"kind": kind, "pid": os.getpid(), **fields}, ensure_ascii=False) + "\n"
    )
    sys.stdout.flush()


def proof(module: str) -> dict:
    from app.settings import catalog_path, env
    from measure.c5 import git_sha, repo_path, sha256
    from meta.meter import Meter
    from worker.brain import card, labels
    from worker.vision import spark

    path = catalog_path().resolve()
    meter = Meter(spend=None)
    return {"module": module, "sha": git_sha(), "catalog_path": repo_path(path), "catalog_sha256": sha256(path),
            "catalog_items": len(card.CATALOG), "labels_catalog_same": labels.load_catalog() == card.CATALOG,
            "session_cap_usd": meter.session_cap, "session_warn_usd": meter.session_warn_at,
            "model_key_set": bool(env("MODEL_API_KEY")) and env("MODEL_API_KEY") != "replace-me",
            "model_spark": env("MODEL_SPARK"), "spark_effort": spark.EFFORT,
            "spark_py_sha256": hashlib.sha256(open(spark.__file__, "rb").read()).hexdigest(),
            "cards_from_replay": env("CARDS_FROM_REPLAY"), "offline_loop": offline_loop()}  # fmt: skip


def offline_loop() -> int | None:
    from app.settings import env

    return int(env("C5_OFFLINE")) if env("C5_OFFLINE") else None


def on_term(signum, frame) -> None:
    from worker.brain import card

    if PROGRESS is not None:
        PROGRESS.n += 1
        say("progress", n=PROGRESS.n, **PROGRESS.line())
    say("waits", waits={sid: dict(s.waits) for sid, s in card.SESSIONS.items()})
    os._exit(0)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in MODULES:
        raise SystemExit(__doc__)
    module = sys.argv[1]
    said = proof(module)
    say("proof", **said)
    if said["offline_loop"] is not None and module == "worker":
        if said["model_key_set"]:
            raise SystemExit("C5_OFFLINE with a model key set: the stand-ins are for unpaid runs only")
        from measure import c5_offline

        say("offline", **c5_offline.install(said["offline_loop"], say))
    if module == "worker":
        from measure import c5_progress

        global PROGRESS
        PROGRESS = c5_progress.install(say)
    signal.signal(signal.SIGTERM, on_term)
    sys.argv = [module, *sys.argv[2:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
