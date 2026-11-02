"""Stand-ins for an unpaid C5 live run (ruling 05:33), installed in the worker process by measure/c5_proc when
C5_OFFLINE=<loop k> is set and the model key is empty. No model is called and no model_call is written: what they
return is stored output of session test1, replayed, never a model result.

- model client: Runner.client_for returns NoModel, whose every call raises RuntimeError as a missing key does, so
  the worker takes its no-key paths (the rule price reader, no product_seen from vision) and no MetaClient exists;
- labels: the labels brain's client is c5_dry.GoldClient over the requested existing streams; the brain, its prompt context and
  its order drafts run for real, the labels are the gold ones;
- cut-out: sam.cut_out returns a stored SAM cut-out of session test1 (data/local/test1/probe/sam.json, mask
  decoded, file on this machine) only when it is the output of the same call: the same source frame (the frame's
  bytes are found by sha256: frames.load is wrapped to note sha256 -> f_<i>.jpg, and the row's t is that i) and the
  same noun (sam.noun of the card's category). The gold is never read and the row's product label is recorded, not
  matched. No such row, or more than one, and it returns None: the card has no image (NOT_RUN), with the reason in a
  C5PROC "cutout" line that also names the source session, frame, png and its sha256.
"""

import hashlib
import json
import re
from pathlib import Path

import httpx

from measure import c5

SAM = c5.ROOT / "data/local/test1/probe/sam.json"
SOURCE = "test1"  # the session the stored output comes from
FRAME = re.compile(r"f_(\d{5})\.jpg$")


def stored_cutouts(sam_json: Path = SAM) -> dict[int, list[dict]]:
    """Source frame t -> the stored cut-outs of it whose mask decoded and whose file is on this machine."""
    out: dict[int, list[dict]] = {}
    for r in json.loads(sam_json.read_text())["rows"]:
        png = sam_json.parent / "sam" / f"cut_{r['t']:03d}_{r['product']}.png"
        if r.get("mask_decoded") and png.exists():
            out.setdefault(r["t"], []).append({"noun": r["noun"], "sku": r["product"], "png": png})
    return out


class NoModel:
    """The worker's model client in an unpaid run."""

    def __getattr__(self, name: str):
        raise RuntimeError(f"C5 offline run: no model client ({name})")


async def register_stored_product(runner, event: dict) -> bool:
    """Idempotently register an actually observed replay row through the normal authenticated producer.

    A sequence or a plausible payload alone is not stored evidence. Read the same session at that sequence,
    compare its canonical body, and preserve the accepted sequence. The server retains the original source
    receipt and still decides epoch/coverage validity; this does not grant a newer epoch to an old row.
    """
    from app.attachments import plain
    from worker.runner import parse_sse

    sid, seq = event["session_id"], event.get("seq")
    if event.get("source") != "replay" or type(seq) is not int or seq <= 0:
        return False
    refs = event.get("caused_by", [])
    known = runner.evidence.frames.get(sid, {})
    if len(refs) != 1 or refs[0] not in known or event.get("frame_ts_ms") != known[refs[0]]["ts_ms"]:
        return False
    body = plain(event)
    found = False
    async with runner.http.stream(
        "GET", f"/api/sessions/{sid}/events", params={"cursor": seq - 1, "follow": "false"}
    ) as response:
        response.raise_for_status()
        async for stored_seq, stored in parse_sse(response.aiter_lines()):
            if stored_seq >= seq:
                found = stored_seq == seq and plain(stored) == body
                break
    if not found:
        return False
    await runner.post([body])
    return runner.evidence.post_sequences.get(event["event_id"]) == seq


def install_frame_replay(runner_class, say) -> None:
    """Only the unpaid stand-in process uses external stored recognitions as producer completions."""
    process = runner_class.process

    async def replay_process(self, event, received_ms, limit_s=None):
        bound = event["session_id"] in self.attachment_auth
        stored_product = bound and event["type"] == "product_seen"
        if stored_product:
            try:
                registered = await register_stored_product(self, event)
            except (httpx.HTTPError, ValueError, KeyError):
                registered = False
            if not registered:
                say("replay_evidence", event_id=event["event_id"], result="refused_stored_product")
                return "failed"
        outcome = await process(self, event, received_ms, limit_s)
        completed = getattr(self, "_c5_stored_terminals", {})
        if stored_product and outcome == "done":
            frame_id = self.evidence.seen_frames.get(event["event_id"])
            terminal = self.evidence.terminals.get(frame_id)
            if terminal and terminal.get("product_seen_event_id") == event["event_id"]:
                completed[frame_id] = dict(terminal)
                self._c5_stored_terminals = completed
                say("replay_evidence", event_id=event["event_id"], result="stored_product_completed")
        elif bound and event["type"] == "frame" and event["event_id"] in completed:
            # A late NoModel failure is still a failed read. It cannot erase a separately completed,
            # admitted stored-product/card chain. No missing/failed product handler creates this entry.
            self.evidence.terminals[event["event_id"]] = dict(completed[event["event_id"]])
            self.evidence.wake()
        return outcome

    runner_class.process = replay_process


class Cutouts:
    """The stored cut-out of the same call (frame and noun); every answer and miss is kept for the run file."""

    def __init__(self, stored: dict[int, list[dict]], noun):
        self.stored, self.noun = stored, noun
        self.index: dict[str, int] = {}
        self.log: list[dict] = []

    def note(self, event: dict, jpeg: bytes) -> None:
        if m := FRAME.search(str(event.get("jpeg_path") or "")):
            self.index[hashlib.sha256(jpeg).hexdigest()] = int(m.group(1))

    def pick(self, jpeg: bytes, category: str) -> tuple[Path | None, dict]:
        sha = hashlib.sha256(jpeg).hexdigest()
        i, noun = self.index.get(sha), self.noun(category)
        row = {"source_session": SOURCE, "frame": i, "frame_sha256": sha, "category": category, "noun": noun,
               "source_t": None, "stored_sku": None, "png": None, "png_sha256": None, "reason": None}  # fmt: skip
        if i is None:
            return None, {**row, "reason": "frame not seen by this worker"}
        same = [r for r in self.stored.get(i, []) if r["noun"] == noun]
        if len(same) != 1:
            many = "several stored cut-outs" if same else "no stored cut-out"
            return None, {**row, "reason": f"{many} of frame {i} for '{noun}'"}
        png = same[0]["png"]
        return png, {**row, "source_t": i, "stored_sku": same[0]["sku"], "png": png.name,
                     "png_sha256": hashlib.sha256(png.read_bytes()).hexdigest()}  # fmt: skip


def install(k: int, say, *, loops: int = 1) -> dict:
    """Install the existing gold streams for a session worker; single-stream callers keep their default."""
    from measure.c5_dry import GoldClient
    from worker import runner
    from worker.brain import labels
    from worker.vision import frames, sam

    if k < 1 or loops < 1:
        raise ValueError("offline stream range must be positive")
    rows = [row for n in range(k, k + loops) for row in c5.streams(n)[0]]
    no_model = NoModel()
    runner.Runner.client_for = lambda self, event: no_model
    install_frame_replay(runner.Runner, say)
    gold_client = GoldClient(rows)
    labels.LABELER.client_for = lambda ctx: gold_client
    cuts = Cutouts(stored_cutouts(), sam.noun)
    load = frames.load

    def noting_load(event: dict) -> bytes:
        jpeg = load(event)
        cuts.note(event, jpeg)
        return jpeg

    async def stored_cut_out(client, jpeg: bytes, category: str):
        from app.contracts import now_ms

        png, row = cuts.pick(jpeg, category)
        cuts.log.append(row)
        say("cutout", **row)
        if png is None:
            return None
        now = now_ms()
        return frames.data_url(png.read_bytes(), "image/png"), (now, now)

    frames.load = noting_load
    sam.cut_out = stored_cut_out
    return {"loop": k, "loops": loops, "gold_rows": len(rows), "cutout_frames": sorted(cuts.stored),
            "model_client": "none: every call raises RuntimeError, as with no key",
            "frame_evidence": "admitted stored products reposted idempotently; failed reads remain failed",
            "labels": "gold labels through the labels brain (GoldClient), stored gold of test1, replayed",
            "cutout": "stored output of session test1 (probe/sam) of the same frame and noun, replayed"}  # fmt: skip
