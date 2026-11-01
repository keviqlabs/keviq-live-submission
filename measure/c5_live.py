"""C5 live run: a running app, one session worker and per-loop media under measure/c5_proc.

The run's per-session cap and warning are MODEL_SESSION_CAP_USD / MODEL_SESSION_WARN_USD of this process's env, both
or neither, 8 / 6 when unset (session_cap). The app is started by the operator with the same and is not on :8001; the
runner prints the cap it runs with, checks /api/cost?session=<run> gives it and that the session is empty before
loop 1. The children get this process's env plus CATALOG_PATH and that cap / warning (CARDS_FROM_REPLAY removed);
each prints a proof line of what it loaded, the cap and warning its Meter took among them, and the runner stops if
it differs (catalog sha256, the labels catalog, cap/warn, git sha, vision effort, the model key set or not).
Without --paid the children run with MODEL_API_KEY empty: 0 USD, the vision call fails and media runs with
--no-voice. The runner then posts the 6b vision rows (measure/c5_dry.media_items)
as product_seen on the loop's clock, each caused by this run's frame at or just before its time, so cards open on
the runtime path (caused_by, not CARDS_FROM_REPLAY), and the stored test1 lines as final speech when they end (the
rule price reader reads them). The worker gets C5_OFFLINE=1 and C5_OFFLINE_LOOPS=<count>: the existing gold rows of every requested stream
through the labels brain, the stored
SAM cut-out of the same frame and noun (measure/c5_offline; never chosen by the gold), and no model client at all;
a card with no such cut-out has no image (NOT_RUN). All of it is stored output of session test1, replayed, not a model result; the shop's route
and the simulated Messenger are the real ones.

The worker starts once at the empty session head and retains its real frame evidence throughout the run.
Per loop: media up; t0 = the first frame's ts_ms minus its
index * 1000 (media posts frame i at t0 + i * 1000); comments and customers on that clock; at t0 + loop + tail
media is stopped if still running, the shop and customers finish, the runner waits at the barrier (Watch.barrier: the
worker has finished every event of the session, by its own count; never on silence alone), every open card gets a
retired version, the barrier again, then SIGUSR1 requests a nonterminating waits snapshot. Per-loop waits,
wait lines and cut-outs are separate; worker progress remains cumulative. The worker gets SIGTERM once at run end
or on failure. A session_cap
refusal ends the run after its loop. A barrier that times out is INCOMPLETE in the loop row, with what was left.
Child logs go to --out next to the evidence files and are not evidence: they may hold utterances and comment text.

Code checks run on keviq_test at the end: the operator holds the keviq_test lock for the whole run. The ledger is
read from DATABASE_URL (the .env the app reads)."""

import asyncio
import bisect
import json
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from measure import c5, c5_bind, c5_proc

log = logging.getLogger("c5.live")
SHOP_DELAY_S = 4.0
FRAME = re.compile(r"f_(\d{5})\.jpg$")
CAP_USD, WARN_USD = 8.0, 6.0


def session_cap(environ=os.environ) -> tuple[float, float, str]:
    """(cap, warning, "env" or "default"): MODEL_SESSION_CAP_USD and MODEL_SESSION_WARN_USD, both set or both unset
    (8 / 6), 0 < warning < cap; anything else stops the run before it starts."""
    raw = [(environ.get(k) or "").strip() for k in ("MODEL_SESSION_CAP_USD", "MODEL_SESSION_WARN_USD")]
    if raw == ["", ""]:
        return CAP_USD, WARN_USD, "default"
    try:
        cap, warn = (float(x) for x in raw)
    except ValueError:
        raise SystemExit(
            "set MODEL_SESSION_CAP_USD and MODEL_SESSION_WARN_USD as numbers, or neither"
        ) from None
    if not 0 < warn < cap:
        raise SystemExit(f"session cap {cap} / warning {warn}: needs 0 < warning < cap")
    return cap, warn, "env"


def check_proof(proof: dict, want: dict) -> list[str]:
    """What in a child's proof line differs from what the run wants (empty: it loaded what the run said)."""
    return [f"{k}: {proof.get(k)!r} != {v!r}" for k, v in want.items() if proof.get(k) != v]


def t0_of(frame: dict) -> int:
    """The loop's t0 from one frame event: media posts frame i (f_<i>.jpg) at t0 + i * 1000."""
    return frame["ts_ms"] - int(FRAME.search(frame["jpeg_path"]).group(1)) * 1000


def write_log(path: Path, lines: list[str]) -> None:
    path.write_text("".join(line + "\n" for line in lines))


class Child:
    """One `python -m measure.c5_proc <module>` process; stdout and stderr kept, its C5PROC lines parsed."""

    def __init__(self, module: str, args: list[str], env: dict, log_path: Path):
        self.module, self.args, self.env, self.log_path = module, args, env, log_path
        self.lines: list[str] = []
        self.said: dict[str, dict] = {}
        self.snapshots: list[dict] = []
        self.wait_lines: list[str] = []
        self.cutouts: list[dict] = []
        self.failures, self.ready, self.summary = 0, asyncio.Event(), None
        self.progress, self.progress_at = None, None  # the worker's last progress line, when it came
        self.proc = self.reader = None

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "measure.c5_proc", self.module, *self.args, cwd=c5.ROOT, env=self.env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )  # fmt: skip
        self.reader = asyncio.create_task(self.read())

    async def read(self) -> None:
        async for raw in self.proc.stdout:
            line = raw.decode(errors="replace").rstrip("\n")
            self.lines.append(line)
            if line.startswith(c5_proc.TAG):
                said = json.loads(line[len(c5_proc.TAG) :])
                if said["kind"] == "progress":
                    self.progress, self.progress_at = said, time.monotonic()
                else:
                    self.said.setdefault(said["kind"], said)
                if said["kind"] == "cutout":
                    self.cutouts.append(said)
                elif said["kind"] == "waits":
                    self.snapshots.append(said)
            elif "price wait:" in line:
                self.wait_lines.append(line[line.index("price wait:") :])
            elif " handler " in line and " failed on " in line:
                self.failures += 1
            elif "worker: consuming" in line:
                self.ready.set()
            elif line.startswith("{'frames'"):
                self.summary = line

    async def said_kind(self, kind: str, timeout_s: float) -> dict:
        end = time.monotonic() + timeout_s
        while kind not in self.said:
            if self.proc.returncode is not None or time.monotonic() > end:
                raise SystemExit(f"{self.module}: no {kind} line (rc {self.proc.returncode})")
            await asyncio.sleep(0.1)
        return self.said[kind]

    async def stop(self, grace_s: float = 10.0) -> tuple[int, bool]:
        """(rc, stopped by us): waits `grace_s` for the process to end, then SIGTERM, then SIGKILL."""
        stopped = False
        try:
            await asyncio.wait_for(self.proc.wait(), grace_s)
        except TimeoutError:
            stopped = True
            self.proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        await self.reader
        write_log(self.log_path, self.lines)
        return self.proc.returncode, stopped

    async def term(self) -> int:
        """SIGTERM now (the worker never ends on its own): it prints its waits line and exits."""
        if self.proc.returncode is None:
            self.proc.send_signal(signal.SIGTERM)
        return (await self.stop(10.0))[0]

    async def snapshot(self, timeout_s: float = 10.0) -> dict:
        """Get a fresh loop-end snapshot without discarding the session's completion evidence."""
        n = len(self.snapshots)
        if self.proc.returncode is not None:
            raise SystemExit(f"worker ended before snapshot (rc {self.proc.returncode})")
        self.proc.send_signal(signal.SIGUSR1)
        async with asyncio.timeout(timeout_s):
            while len(self.snapshots) == n:
                if self.proc.returncode is not None:
                    raise SystemExit(f"worker ended during snapshot (rc {self.proc.returncode})")
                await asyncio.sleep(0.01)
        return self.snapshots[n]


class Watch:
    """The session's new events every 0.5 s (follow=false) into the feed and the shop."""

    def __init__(self, http, sid: str, feed, shop):
        self.http, self.sid, self.feed, self.shop = http, sid, feed, shop
        self.frames: list[dict] = []
        self.seqs: list[int] = []  # the session's seqs seen, in order; the last is the head
        self.polls, self.capped, self.cap = 0, None, asyncio.Event()
        self.stopping = asyncio.Event()

    async def poll(self) -> None:
        from worker.runner import parse_sse

        params = {"cursor": self.feed.cursor, "follow": "false"}
        async with self.http.stream("GET", f"/api/sessions/{self.sid}/events", params=params) as resp:
            resp.raise_for_status()
            async for seq, ev in parse_sse(resp.aiter_lines()):
                self.feed.add(seq, ev)
                self.shop.see(ev)
                if seq is not None:
                    self.seqs.append(seq)
                if ev["type"] == "frame":
                    self.frames.append(ev)
                elif ev["type"] == "model_call" and str(ev.get("error") or "").startswith("session_cap"):
                    self.capped = self.capped or ev["event_id"]
                    self.cap.set()
        self.polls += 1

    async def run(self) -> None:
        import httpx

        while not self.stopping.is_set():
            try:
                await self.poll()
            except httpx.HTTPError as exc:
                log.warning("watch: %s", exc)
            try:
                await asyncio.wait_for(self.stopping.wait(), 0.5)
            except TimeoutError:
                pass

    async def halt(self, task: asyncio.Task) -> None:
        """Stops run() between polls: a poll in flight ends on its own, it is never cancelled mid-query."""
        self.stopping.set()
        try:
            await task
        except Exception as exc:
            log.warning("watch: %s", exc)

    async def frame_after(self, n: int, timeout_s: float) -> dict:
        end = time.monotonic() + timeout_s
        while len(self.frames) <= n:
            if time.monotonic() > end:
                raise SystemExit(f"no frame from media in {timeout_s} s")
            await asyncio.sleep(0.1)
        return self.frames[n]

    def at_head(self, p: dict, head: int | None) -> bool:
        """The worker (--session: it reads this session only) has finished the session up to its head."""
        if p.get("delivered"):
            return p.get("done_seq") == head
        # nothing delivered yet: fine only if the worker started at or after the head
        return p.get("start_seq") is not None and (head is None or p["start_seq"] >= head)

    def check(self, worker: "Child", tasks: list[set]) -> dict:
        """One look at the work left: the worker's progress line (measure/c5_progress) against the session head."""
        p = worker.progress or {}
        head = self.seqs[-1] if self.seqs else None
        done = p.get("done_seq")
        at_head = self.at_head(p, head)
        age = None if worker.progress_at is None else int((time.monotonic() - worker.progress_at) * 1000)
        busy = sum(not t.done() for s in tasks for t in s)
        row = {"head_seq": head, "done_seq": done,
               "behind": len(self.seqs) - (bisect.bisect_right(self.seqs, done) if done is not None else 0),
               **{k: p.get(k) for k in ("delivered", "finished", "errors", "skipped", "stale", "pending", "running",
                                        "background", "queued")},
               "tasks_running": busy, "progress_age_ms": age}  # fmt: skip
        idle = p.get("pending") == 0 and p.get("background") == 0 and p.get("queued") == 0 and busy == 0
        ok = bool(p) and age is not None and age <= 2000 and idle and at_head
        return {**row, "ok": ok, "n": p.get("n", 0), "polls": self.polls}

    async def barrier(self, worker: "Child", tasks: list[set], timeout_s: float = 120.0) -> dict:
        """The loop's work is done, never just quiet (RC2): the worker's last progress line under 2 s old, nothing
        pending, no background task of the worker left and nothing queued, its done_seq at the session head, and no
        shop/customer task running; seen on two checks with the same head, a newer progress line and two polls of
        the session between them. At the timeout the result is INCOMPLETE with what was still left (events behind,
        pending, running, background, queued, tasks)."""
        start = time.monotonic()
        first = None
        while True:
            row = self.check(worker, tasks)
            if (row["ok"] and first and first["head_seq"] == row["head_seq"] and row["n"] > first["n"]
                    and self.polls >= first["polls"] + 2):  # fmt: skip
                status = "SETTLED"
                break
            if time.monotonic() - start >= timeout_s:
                status = "INCOMPLETE"
                break
            if not row["ok"]:
                first = None
            elif not first or first["head_seq"] != row["head_seq"]:
                first = row
            await asyncio.sleep(0.5)
        drop = ("ok", "n", "polls")
        return {"status": status, "waited_ms": int((time.monotonic() - start) * 1000),
                **{k: v for k, v in row.items() if k not in drop}}  # fmt: skip


async def read_all(http, sid: str) -> list[dict]:
    from worker.runner import parse_sse

    params = {"cursor": 0, "follow": "false"}
    async with http.stream("GET", f"/api/sessions/{sid}/events", params=params) as resp:
        resp.raise_for_status()
        return [ev async for _, ev in parse_sse(resp.aiter_lines())]


async def post_comments(http, sid: str, items: list[tuple[int, dict]], headers: dict | None = None) -> int:
    """Posts each comment when due (headers: the source bearer of a bound session); returns the worst lateness
    in ms."""
    from app.contracts import now_ms

    worst = 0
    for due, ev in sorted(items, key=lambda x: x[0]):
        await asyncio.sleep(max(due - now_ms(), 0) / 1000)
        worst = max(worst, now_ms() - due)
        (await http.post(f"/api/sessions/{sid}/events", json=[ev], headers=headers)).raise_for_status()
    return worst


def replay_seen(
    sid: str, k: int, t0: int, loop_ms: int, etype: str = "product_seen", observations: Path | None = None
) -> list[tuple[int, dict]]:
    """(post at, event) for the 6b new rows (or, etype "speech", the test1 lines) that start in the loop, on its
    clock t0; with observations, the rows a stored run observed instead (measure.c5_frozen)."""
    from measure import c5_dry, c5_frozen

    source = c5_frozen.items(observations, sid, k, t0) if observations else c5_dry.media_items(sid, k, t0)
    return [(due, ev) for due, ev in source if ev["type"] == etype and ev["ts_ms"] - t0 < loop_ms]


def frozen_note(etype: str, observations: Path) -> str:
    return f"stored {etype} events of {observations.name} (measure.c5_frozen), replayed on the loop clock" + (
        ", caused by this run's frames" if etype == "product_seen" else "; media --no-voice"
    )


def frozen_sha(observations: Path) -> str:
    from measure import c5_frozen

    return c5_frozen.sha256(observations)


def caused(ev: dict, frames: list[dict]) -> dict | None:
    """The product_seen caused by the session's last frame at or before its time; None when there is none."""
    from app.contracts import validate_event

    before = [f for f in frames if f["session_id"] == ev["session_id"] and f["ts_ms"] <= ev["ts_ms"]]
    if not before:
        return None
    frame = max(before, key=lambda f: f["ts_ms"])
    return validate_event({**ev, "caused_by": [frame["event_id"]], "frame_ts_ms": frame["ts_ms"]})


async def post_seen(http, sid: str, items: list[tuple[int, dict]], frames: list[dict], start: int,
                    wait_s: float = 2.0, headers: dict | None = None) -> dict:  # fmt: skip
    """Posts each product_seen when due, caused by a frame of this loop (frames[start:], the watch's list);
    headers: the source bearer of a bound session.

    A label whose own frame has not come through the watch yet waits up to wait_s for it, then takes the frame just
    before; a label before the loop's first frame is not posted."""
    from app.contracts import now_ms

    posted, no_frame = 0, []
    for due, ev in sorted(items, key=lambda x: x[0]):
        await asyncio.sleep(max(due - now_ms(), 0) / 1000)
        end = time.monotonic() + wait_s
        while not any(f["ts_ms"] >= ev["ts_ms"] for f in frames[start:]):
            if time.monotonic() > end:
                break
            await asyncio.sleep(0.1)
        if (seen := caused(ev, frames[start:])) is None:
            no_frame.append(ev["event_id"])
            continue
        (await http.post(f"/api/sessions/{sid}/events", json=[seen], headers=headers)).raise_for_status()
        posted += 1
    return {"posted": posted, "no_frame": no_frame}


async def main(base_url: str, run: str, loops: int, catalog: Path, out: Path, check_code: bool,
               seconds: float | None = None, paid: bool = False, observations: Path | None = None) -> int:  # fmt: skip
    import httpx

    from app import settings
    from app.contracts import now_ms
    from measure.c5_shop import Customers, Feed, Gold, Loops, Shop

    if urlsplit(base_url).port == 8001:
        raise SystemExit("refusing :8001 (it gets no C5 events)")
    settings.load_dotenv(c5.ROOT / ".env")
    cap, warn, cap_from = session_cap()
    print(f"session cap {cap} USD, warning {warn} USD ({cap_from})", flush=True)
    env = {**os.environ, "CATALOG_PATH": str(catalog), "MODEL_SESSION_CAP_USD": str(cap),
           "MODEL_SESSION_WARN_USD": str(warn), "PYTHONUNBUFFERED": "1"}  # fmt: skip
    env.pop("CARDS_FROM_REPLAY", None)
    if not paid:
        env["MODEL_API_KEY"] = ""
    env.pop("C5_OFFLINE", None)
    env.pop("C5_OFFLINE_LOOPS", None)
    bind = c5_bind.credentials()  # the children inherit the C5_BIND_* variables through env
    if bind is not None and bind["session"] != run:
        raise SystemExit(f"C5_BIND_SESSION {bind['session']!r} is not the run {run!r}")
    bearer = c5_bind.header(bind) if bind else None  # generic event posts only: console routes refuse it
    sha, tree = c5.git_sha(), c5.git_state()  # at the start: the tree the run ran on
    want = {"catalog_sha256": c5.sha256(catalog), "labels_catalog_same": True, "session_cap_usd": cap,
            "session_warn_usd": warn, "sha": sha, "spark_effort": "minimal", "model_key_set": paid}  # fmt: skip
    gold = Gold.load(c5.C5, c5.LABELS)
    full_ms = gold.windows["loop_ms"]
    loop_ms = min(full_ms, int(seconds * 1000)) if seconds else full_ms
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    rows_by_loop, msgs_by_loop, loop_rows, children = {}, {}, [], []
    async with httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(10, read=30)) as http:
        health = (await http.get("/healthz")).json()
        cost = (await http.get("/api/cost", params={"session": run})).json()
        got = (cost.get("session_cap_usd"), cost.get("session_warn_usd"))
        if got != (cap, warn):
            raise SystemExit(f"app cap/warn for {run}: {got[0]}/{got[1]}, start it with "
                             f"MODEL_SESSION_CAP_USD={cap:g} MODEL_SESSION_WARN_USD={warn:g}")  # fmt: skip
        if await read_all(http, run):
            raise SystemExit(f"{run} is not empty")
        state = (await http.get(f"/api/sessions/{run}/state")).json()
        if bool(state.get("attachment_enabled")) != bool(bind):
            raise SystemExit(f"{run}: attachment_enabled {state.get('attachment_enabled')!r} in the app, the "
                             f"runner is {'bound' if bind else 'not bound'} (start the app with the same "
                             f"C5_BIND_* variables through measure.c5_bind:app)")  # fmt: skip
        feed, clock = Feed(), Loops()
        shop = Shop(http, run, gold, feed, clock, delay_s=SHOP_DELAY_S)
        customers = Customers(http, run, feed)
        watch = Watch(http, run, feed, shop)
        watching = asyncio.create_task(watch.run())
        seen = speaking = posting = None
        worker = None
        worker_rc = None
        previous_waits = {}
        try:
            # One session/epoch owns one completion ledger, including every earlier loop's frames.
            worker_env = env if paid else {**env, "C5_OFFLINE": "1", "C5_OFFLINE_LOOPS": str(loops)}
            worker_want = {
                **want,
                "offline_loop": None if paid else 1,
                "offline_loops": None if paid else loops,
            }
            worker = Child(
                "worker", ["--base-url", base_url, "--session", run], worker_env, out / f"{run}-worker.log"
            )
            children.append(worker)
            await worker.start()
            worker_proofs = {"worker": await worker.said_kind("proof", 60)}
            if not paid:
                worker_proofs["offline"] = await worker.said_kind("offline", 60)
            if bind:
                worker_proofs["worker_attachment"] = await worker.said_kind("attachment", 60)
            if bad := check_proof(worker_proofs["worker"], worker_want):
                raise SystemExit(f"worker: {bad}")
            await asyncio.wait_for(worker.ready.wait(), 30)
            for k in range(1, loops + 1):
                rows, msgs = c5.streams(k)
                rows_by_loop[k], msgs_by_loop[k] = rows, msgs
                loop_env = env if paid else {**env, "C5_OFFLINE": str(k)}
                loop_want = {**want, "offline_loop": None if paid else k}
                proofs = dict(worker_proofs)
                wait_start, cutout_start, failures_start = (
                    len(worker.wait_lines),
                    len(worker.cutouts),
                    worker.failures,
                )
                media = Child("worker.media", ["--session", "test1", "--target", run, "--base-url", base_url,
                                               *(["--seconds", str(seconds)] if seconds else []),
                                               *([] if paid else ["--no-voice"])], loop_env,
                              out / f"{run}-loop{k}-media.log")  # fmt: skip
                children.append(media)
                n_frames = len(watch.frames)
                await media.start()
                proofs["media"] = await media.said_kind("proof", 60)
                if bad := check_proof(proofs["media"], loop_want):
                    raise SystemExit(f"media, loop {k}: {bad}")
                if bind:
                    proofs["media_attachment"] = await media.said_kind("attachment", 60)
                t0 = t0_of(await watch.frame_after(n_frames, 120))
                clock.t0.append(t0)
                end = t0 + loop_ms
                print(f"loop {k}: t0 {t0}", flush=True)
                customers.start([m for m in msgs if m["not_before_ms"] < loop_ms], t0, end + c5.TAIL_MS, k)
                due = [r for r in rows if r.get("arrives_ms", r["ts_ms"]) < loop_ms]
                posting = asyncio.create_task(
                    post_comments(http, run, c5.comment_schedule(due, run, t0), headers=bearer)
                )
                seen_items = [] if paid else replay_seen(run, k, t0, loop_ms, observations=observations)
                seen = None if paid else asyncio.create_task(
                    post_seen(http, run, seen_items, watch.frames, n_frames, headers=bearer))  # fmt: skip
                speech = [] if paid else replay_seen(run, k, t0, loop_ms, "speech", observations)
                speaking = (
                    None if paid else asyncio.create_task(post_comments(http, run, speech, headers=bearer))
                )
                try:  # the loop end, or a session_cap refusal
                    await asyncio.wait_for(watch.cap.wait(), max(end + c5.TAIL_MS - now_ms(), 0) / 1000)
                except TimeoutError:
                    pass
                media_rc, media_stopped = await media.stop(30.0 if not watch.capped else 0.0)
                if watch.capped:
                    for task in (posting, seen, speaking):
                        if task:
                            task.cancel()
                late_ms = None if watch.capped else await posting
                seen_log = None if watch.capped or not seen else await seen
                speech_log = None if watch.capped or not speaking else {"posted": len(speech),
                                                                         "late_max_ms": await speaking}  # fmt: skip
                ends = [await watch.barrier(worker, [shop.tasks, customers.tasks])]
                retired = c5.retire_events(feed, run)
                if retired:
                    (
                        await http.post(f"/api/sessions/{run}/events", json=retired, headers=bearer)
                    ).raise_for_status()
                ends.append(await watch.barrier(worker, [shop.tasks, customers.tasks]))
                calm = all(e["status"] == "SETTLED" for e in ends)
                cumulative_waits = (await worker.snapshot()).get("waits", {}).get(run, {})
                snapshot = {
                    reason: count - previous_waits.get(reason, 0)
                    for reason, count in cumulative_waits.items()
                }
                previous_waits = dict(cumulative_waits)
                loop_rows.append({"k": k, "t0_ms": t0, "end_ms": now_ms(), "settled": calm, "barrier": ends,
                                  "worker_progress": worker.progress,
                                  "worker_progress_scope": "cumulative session",
                                  "wait_lines": worker.wait_lines[wait_start:], "waits": snapshot,
                                  "waits_line": True, "retired": len(retired),
                                  "runner_failures": worker.failures - failures_start, "comments_late_max_ms": late_ms,
                                  "media": {"rc": media_rc, "stopped": media_stopped, "summary": media.summary},
                                  "worker_rc": worker_rc, "proofs": proofs, "capped": watch.capped,
                                  "replay_seen": seen_log, "replay_speech": speech_log,
                                  "cutouts": worker.cutouts[cutout_start:]})  # fmt: skip
                print(
                    f"loop {k}: done, {'/'.join(e['status'] for e in ends)} behind {ends[-1]['behind']}, retired "
                    f"{len(retired)}, waits {snapshot}, failures {worker.failures}"
                )
                if watch.capped:
                    print(f"loop {k}: session_cap refusal {watch.capped}, run stops")
                    break
        finally:
            pending = [task for task in (posting, seen, speaking) if task and not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            actors = [task for task in [*shop.tasks, *customers.tasks] if not task.done()]
            for task in actors:
                task.cancel()
            if actors:
                await asyncio.gather(*actors, return_exceptions=True)
            await watch.halt(watching)  # not cancel(): the poll in flight ends before read_all queries
            for child in children:  # a child killed here keeps its log too
                if child.proc:
                    if child is worker:
                        worker_rc = await child.term()
                    elif child.proc.returncode is None:
                        await child.stop(0)
            for row in loop_rows:
                row["worker_rc"] = worker_rc if worker_rc is not None else worker.proc.returncode
        events = await read_all(http, run)
        cost_api = (await http.get("/api/cost", params={"session": run})).json()
    run_file = {
        "run": run, "session_id": run, "sha": sha, "git": tree, "loops": loop_rows,
        "config": {"mode": "live", "base_url_port": urlsplit(base_url).port, "healthz": health,
                   "attachment": c5_bind.public(bind) if bind else None,
                   "catalog_path": c5.repo_path(catalog), "catalog_sha256": want["catalog_sha256"],
                   "vision": "worker vision brain (spark), effort minimal", "effort": "minimal", "fps": 1,
                   "product_seen": "worker vision" if paid else frozen_note("product_seen", observations)
                   if observations else "stored output of session test1 (6b rows, "
                   "c5_dry.media_items), replayed by the runner, caused by this run's frames",
                   "cutout": "on a card gone live (model call)" if paid else "stored output of session test1 "
                   "(probe/sam cut-out of the same source frame and noun, not chosen by the gold), replayed; none: no "
                   "image (NOT_RUN)", "model_client": "model client" if paid else "none (measure/c5_offline.NoModel)",
                   "card_line": False, "labels": "labels brain with the model" if paid else "gold labels through "
                   "the labels brain (GoldClient), stored gold of test1, replayed",
                   "speech": "media voice (Voice Transcribe)" if paid else frozen_note("speech", observations)
                   if observations else "stored output of session test1 "
                   "(probe/voice.json), replayed on the loop clock; rule price reader (no key); media --no-voice",
                   "observations": None if paid or not observations else
                   {"path": str(observations), "sha256": frozen_sha(observations)},
                   "cap_usd": cap, "warn_usd": warn, "cap_from": cap_from, "paid": paid, "seconds": seconds,
                   "restart": "one worker per session; media process per loop", "shop_delay_s": SHOP_DELAY_S,
                   "tail_ms": c5.TAIL_MS, "loop_ms": loop_ms},
        "cost_api": cost_api, "customers": customers.log, "shop": shop.log,
    }  # fmt: skip
    c5.finish(events, run_file, rows_by_loop, msgs_by_loop, out, settings.database_url(), check_code)
    return 0
