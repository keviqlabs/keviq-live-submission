"""Stand-ins for an unpaid C5 live run (HQ 05:33), installed in the worker process by measure/c5_proc when
C5_OFFLINE=<loop k> is set and the model key is empty. No model is called and no model_call is written: what they
return is stored output of session test1, replayed, never a model result.

- model client: Runner.client_for returns NoModel, whose every call raises RuntimeError as a missing key does, so
  the worker takes its no-key paths (the rule price reader, no product_seen from vision) and no MetaClient exists;
- labels: the labels brain's client is c5_dry.GoldClient over loop k's gold rows; the brain, its prompt context and
  its order drafts run for real, the labels are the gold ones;
- cut-out: sam.cut_out returns the stored SAM cut-out (data/local/test1/probe/sam/cut_<t>_<sku>.png, mask decoded)
  of the item a card opened at the frame stands for (Gold.card_item, as the scorer: an item's span starts at its
  change band, so a frame in the band SP01 -> SP02 is SP02's although SP01 is still held), the one nearest in t, when
  that item is of the card's category (membership, as card.match). Otherwise it returns None and the card has no
  image: NOT_RUN, with the reason in a C5PROC "cutout" line. The frame is found by its bytes: frames.load is wrapped to note sha256 -> f_<i>.jpg.
"""

import hashlib
import json
import re
from pathlib import Path

from measure import c5

SAM = c5.ROOT / "data/local/test1/probe/sam.json"
FRAME = re.compile(r"f_(\d{5})\.jpg$")


def stored_cutouts(sam_json: Path = SAM) -> dict[str, list[tuple[int, Path]]]:
    """sku -> [(t, png)] of the stored cut-outs whose mask decoded and whose file is on this machine."""
    out: dict[str, list[tuple[int, Path]]] = {}
    for r in json.loads(sam_json.read_text())["rows"]:
        png = sam_json.parent / "sam" / f"cut_{r['t']:03d}_{r['product']}.png"
        if r.get("mask_decoded") and png.exists():
            out.setdefault(r["product"], []).append((r["t"], png))
    return out


class NoModel:
    """The worker's model client in an unpaid run."""

    def __getattr__(self, name: str):
        raise RuntimeError(f"C5 offline run: no model client ({name})")


class Cutouts:
    """The stored cut-out for a frame, by the item held at it; every answer and miss is kept for the run file."""

    def __init__(self, gold, catalog: list[dict], stored: dict[str, list[tuple[int, Path]]]):
        self.gold, self.stored = gold, stored
        self.categories = {i["sku"]: i.get("categories") or [i["category"]] for i in catalog}
        self.index: dict[str, int] = {}
        self.log: list[dict] = []

    def note(self, event: dict, jpeg: bytes) -> None:
        if m := FRAME.search(str(event.get("jpeg_path") or "")):
            self.index[hashlib.sha256(jpeg).hexdigest()] = int(m.group(1))

    def pick(self, jpeg: bytes, category: str) -> tuple[Path | None, dict]:
        i = self.index.get(hashlib.sha256(jpeg).hexdigest())
        row = {"frame": i, "category": category, "item": None, "held": None, "source_t": None, "png": None,
               "reason": None}  # fmt: skip
        if i is None:
            return None, {**row, "reason": "frame not seen by this worker"}
        row["held"] = self.gold.held_at(i * 1000)
        item = row["item"] = self.gold.card_item(i * 1000)
        if item is None:
            return None, {**row, "reason": "no item for a card at this frame"}
        if category not in self.categories.get(item, []):
            return None, {**row, "reason": f"item {item} is not of category {category}"}
        if not self.stored.get(item):
            return None, {**row, "reason": f"no stored cut-out of {item} on this machine"}
        t, png = min(self.stored[item], key=lambda x: (abs(x[0] - i), x[0]))
        return png, {**row, "source_t": t, "png": png.name}


def install(k: int, say) -> dict:
    """Patches the worker modules for loop k; returns what it installed (counts and sources, no text)."""
    from measure.c5_dry import GoldClient
    from measure.c5_shop import Gold
    from worker import runner
    from worker.brain import card, labels
    from worker.vision import frames, sam

    rows = c5.streams(k)[0]
    no_model = NoModel()
    runner.Runner.client_for = lambda self, event: no_model
    gold_client = GoldClient(rows)
    labels.LABELER.client_for = lambda ctx: gold_client
    cuts = Cutouts(Gold.load(c5.C5, c5.LABELS), card.CATALOG, stored_cutouts())
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
    return {"loop": k, "gold_rows": len(rows), "cutout_items": sorted(cuts.stored),
            "model_client": "none: every call raises RuntimeError, as with no key",
            "labels": "gold labels through the labels brain (GoldClient), stored gold of test1, replayed",
            "cutout": "stored output of session test1 (probe/sam), replayed"}  # fmt: skip
