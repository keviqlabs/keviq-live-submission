"""C5 live run: a running app, and per loop k the worker (--session) and media, each under measure/c5_proc.

The app is started by the operator with MODEL_SESSION_CAP_USD=8 MODEL_SESSION_WARN_USD=6 and is not on :8001; the
runner checks /api/cost?session=<run> gives 8.0/6.0 and that the session is empty before loop 1. The children get
this process's env plus CATALOG_PATH and the cap 8 / warning 6 (CARDS_FROM_REPLAY removed); each prints a proof
line of what it loaded and the runner stops if it differs (catalog sha256, the labels catalog, cap/warn, git sha,
vision effort, the model key set or not). Without --paid the children run with MODEL_API_KEY empty: 0 USD, the
vision and voice calls fail or are skipped, so it tests the plumbing only.

Per loop: worker up (it reads from the head, as after a restart), media up; t0 = the first frame's ts_ms minus its
index * 1000 (media posts frame i at t0 + i * 1000); comments and customers on that clock; at t0 + loop + tail
media is stopped if still running, the shop and customers finish, every open card gets a retired version, then
the worker gets SIGTERM and prints its waits snapshot (HQ419). A session_cap refusal ends the run after its loop.
Child logs go to --out next to the evidence files and are not evidence: they may hold utterances and comment text.

Code checks run on keviq_test at the end: the operator holds the keviq_test lock for the whole run. The ledger is
read from DATABASE_URL (the .env the app reads)."""

import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from measure import c5, c5_proc

log = logging.getLogger("c5.live")
SHOP_DELAY_S = 4.0
FRAME = re.compile(r"f_(\d{5})\.jpg$")


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
        self.wait_lines: list[str] = []
        self.failures, self.ready, self.summary = 0, asyncio.Event(), None
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
                self.said.setdefault(said["kind"], said)
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


class Watch:
    """The session's new events every 0.5 s (follow=false) into the feed and the shop."""

    def __init__(self, http, sid: str, feed, shop):
        self.http, self.sid, self.feed, self.shop = http, sid, feed, shop
        self.frames: list[dict] = []
        self.last_new, self.capped, self.cap = time.monotonic(), None, asyncio.Event()

    async def poll(self) -> None:
        from worker.runner import parse_sse

        n = 0
        params = {"cursor": self.feed.cursor, "follow": "false"}
        async with self.http.stream("GET", f"/api/sessions/{self.sid}/events", params=params) as resp:
            resp.raise_for_status()
            async for seq, ev in parse_sse(resp.aiter_lines()):
                self.feed.add(seq, ev)
                self.shop.see(ev)
                n += 1
                if ev["type"] == "frame":
                    self.frames.append(ev)
                elif ev["type"] == "model_call" and str(ev.get("error") or "").startswith("session_cap"):
                    self.capped = self.capped or ev["event_id"]
                    self.cap.set()
        if n:
            self.last_new = time.monotonic()

    async def run(self) -> None:
        import httpx

        while True:
            try:
                await self.poll()
            except httpx.HTTPError as exc:
                log.warning("watch: %s", exc)
            await asyncio.sleep(0.5)

    async def frame_after(self, n: int, timeout_s: float) -> dict:
        end = time.monotonic() + timeout_s
        while len(self.frames) <= n:
            if time.monotonic() > end:
                raise SystemExit(f"no frame from media in {timeout_s} s")
            await asyncio.sleep(0.1)
        return self.frames[n]

    async def quiet(self, tasks: list[set], calm_s: float = 3.0, timeout_s: float = 120.0) -> bool:
        """No new event for calm_s and every shop/customer task done."""
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if time.monotonic() - self.last_new >= calm_s and all(t.done() for s in tasks for t in s):
                return True
            await asyncio.sleep(0.5)
        return False


async def read_all(http, sid: str) -> list[dict]:
    from worker.runner import parse_sse

    params = {"cursor": 0, "follow": "false"}
    async with http.stream("GET", f"/api/sessions/{sid}/events", params=params) as resp:
        resp.raise_for_status()
        return [ev async for _, ev in parse_sse(resp.aiter_lines())]


async def post_comments(http, sid: str, items: list[tuple[int, dict]]) -> int:
    """Posts each comment when due; returns the worst lateness in ms."""
    from app.contracts import now_ms

    worst = 0
    for due, ev in sorted(items, key=lambda x: x[0]):
        await asyncio.sleep(max(due - now_ms(), 0) / 1000)
        worst = max(worst, now_ms() - due)
        (await http.post(f"/api/sessions/{sid}/events", json=[ev])).raise_for_status()
    return worst


async def main(base_url: str, run: str, loops: int, catalog: Path, out: Path, check_code: bool,
               seconds: float | None = None, paid: bool = False) -> int:  # fmt: skip
    import httpx

    from app import settings
    from app.contracts import now_ms
    from measure.c5_shop import Customers, Feed, Gold, Loops, Shop

    if urlsplit(base_url).port == 8001:
        raise SystemExit("refusing :8001 (it gets no C5 events)")
    settings.load_dotenv(c5.ROOT / ".env")
    env = {**os.environ, "CATALOG_PATH": str(catalog), "MODEL_SESSION_CAP_USD": "8",
           "MODEL_SESSION_WARN_USD": "6", "PYTHONUNBUFFERED": "1"}  # fmt: skip
    env.pop("CARDS_FROM_REPLAY", None)
    if not paid:
        env["MODEL_API_KEY"] = ""
    sha = c5.git_sha()
    want = {"catalog_sha256": c5.sha256(catalog), "labels_catalog_same": True, "session_cap_usd": 8.0,
            "session_warn_usd": 6.0, "sha": sha, "spark_effort": "minimal", "model_key_set": paid}  # fmt: skip
    gold = Gold.load(c5.C5, c5.LABELS)
    full_ms = gold.windows["loop_ms"]
    loop_ms = min(full_ms, int(seconds * 1000)) if seconds else full_ms
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    rows_by_loop, msgs_by_loop, loop_rows, children = {}, {}, [], []
    async with httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(10, read=30)) as http:
        health = (await http.get("/healthz")).json()
        cost = (await http.get("/api/cost", params={"session": run})).json()
        if (cost.get("session_cap_usd"), cost.get("session_warn_usd")) != (8.0, 6.0):
            raise SystemExit(f"app cap/warn for {run}: {cost.get('session_cap_usd')}/{cost.get('session_warn_usd')}"
                             ", start it with MODEL_SESSION_CAP_USD=8 MODEL_SESSION_WARN_USD=6")  # fmt: skip
        if await read_all(http, run):
            raise SystemExit(f"{run} is not empty")
        feed, clock = Feed(), Loops()
        shop = Shop(http, run, gold, feed, clock, delay_s=SHOP_DELAY_S)
        customers = Customers(http, run, feed)
        watch = Watch(http, run, feed, shop)
        watching = asyncio.create_task(watch.run())
        try:
            for k in range(1, loops + 1):
                rows, msgs = c5.streams(k)
                rows_by_loop[k], msgs_by_loop[k] = rows, msgs
                worker = Child("worker", ["--base-url", base_url, "--session", run], env,
                               out / f"{run}-loop{k}-worker.log")  # fmt: skip
                children.append(worker)
                await worker.start()
                proofs = {"worker": await worker.said_kind("proof", 60)}
                if bad := check_proof(proofs["worker"], want):
                    raise SystemExit(f"worker, loop {k}: {bad}")
                await asyncio.wait_for(worker.ready.wait(), 30)
                media = Child("worker.media", ["--session", "test1", "--target", run, "--base-url", base_url,
                                               *(["--seconds", str(seconds)] if seconds else [])], env,
                              out / f"{run}-loop{k}-media.log")  # fmt: skip
                children.append(media)
                n_frames = len(watch.frames)
                await media.start()
                proofs["media"] = await media.said_kind("proof", 60)
                if bad := check_proof(proofs["media"], want):
                    raise SystemExit(f"media, loop {k}: {bad}")
                t0 = t0_of(await watch.frame_after(n_frames, 120))
                clock.t0.append(t0)
                end = t0 + loop_ms
                print(f"loop {k}: t0 {t0}", flush=True)
                customers.start([m for m in msgs if m["not_before_ms"] < loop_ms], t0, end + c5.TAIL_MS, k)
                due = [r for r in rows if r.get("arrives_ms", r["ts_ms"]) < loop_ms]
                posting = asyncio.create_task(post_comments(http, run, c5.comment_schedule(due, run, t0)))
                try:  # the loop end, or a session_cap refusal
                    await asyncio.wait_for(watch.cap.wait(), max(end + c5.TAIL_MS - now_ms(), 0) / 1000)
                except TimeoutError:
                    pass
                media_rc, media_stopped = await media.stop(30.0 if not watch.capped else 0.0)
                if watch.capped:
                    posting.cancel()
                late_ms = None if watch.capped else await posting
                calm = await watch.quiet([shop.tasks, customers.tasks])
                retired = c5.retire_events(feed, run)
                if retired:
                    (await http.post(f"/api/sessions/{run}/events", json=retired)).raise_for_status()
                calm = await watch.quiet([shop.tasks, customers.tasks]) and calm
                worker_rc = await worker.term()
                snapshot = (worker.said.get("waits") or {}).get("waits", {}).get(run, {})
                loop_rows.append({"k": k, "t0_ms": t0, "end_ms": now_ms(), "settled": calm,
                                  "wait_lines": worker.wait_lines, "waits": snapshot,
                                  "waits_line": "waits" in worker.said, "retired": len(retired),
                                  "runner_failures": worker.failures, "comments_late_max_ms": late_ms,
                                  "media": {"rc": media_rc, "stopped": media_stopped, "summary": media.summary},
                                  "worker_rc": worker_rc, "proofs": proofs, "capped": watch.capped})  # fmt: skip
                print(f"loop {k}: done, retired {len(retired)}, waits {snapshot}, failures {worker.failures}")
                if watch.capped:
                    print(f"loop {k}: session_cap refusal {watch.capped}, run stops")
                    break
        finally:
            watching.cancel()
            for child in children:
                if child.proc and child.proc.returncode is None:
                    child.proc.kill()
        events = await read_all(http, run)
        cost_api = (await http.get("/api/cost", params={"session": run})).json()
    run_file = {
        "run": run, "session_id": run, "sha": sha, "loops": loop_rows,
        "config": {"mode": "live", "base_url_port": urlsplit(base_url).port, "healthz": health,
                   "catalog_path": c5.repo_path(catalog), "catalog_sha256": want["catalog_sha256"],
                   "vision": "worker vision brain (spark), effort minimal", "effort": "minimal", "fps": 1,
                   "card_line": False, "labels": "labels brain with the model" if paid else "labels brain, no key",
                   "speech": "media voice (Voice Transcribe)" if paid else "media voice skipped (no key)",
                   "cap_usd": 8.0, "warn_usd": 6.0, "paid": paid, "seconds": seconds,
                   "restart": "worker and media processes per loop", "shop_delay_s": SHOP_DELAY_S,
                   "tail_ms": c5.TAIL_MS, "loop_ms": loop_ms},
        "cost_api": cost_api, "customers": customers.log, "shop": shop.log,
    }  # fmt: skip
    c5.finish(events, run_file, rows_by_loop, msgs_by_loop, out, settings.database_url(), check_code)
    return 0
