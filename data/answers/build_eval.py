"""Build the offline eval set for answers about the item on screen: python data/answers/build_eval.py

Input: the C5 comment streams (data/comments/c5/stream-1..5.jsonl), their product windows and catalog. Output:
data/answers/eval-c5.jsonl, one row per event of each loop, in time order (loop-local ms):
  show     the item really on screen from now on (truth, from windows.json)
  card     a synthetic card event (contracts/events/card.schema.json) as the card stream would carry it
  comment  a C5 comment, or an added trap, with the gold the scorer uses

Card timeline per product appearance: a draft (no price) 1.5 s after the switch, the approved card 4 s after it.
SP03 stays a draft in loops 1, 3 and 5. SP09 and SP10 are approved without SKU or variants, so their stock is not
known. In even loops the seller sets the first size of SP01-SP06 to 0 at 8 s. At the second price change of SP01,
SP04 and SP09 the seller lowers the price by 20 000. After every edit the version before it arrives again 300 ms
late. The second SP01 appearance re-sends the card with a new frame time.

Gold kind of a C5 question comes from the template it was written from (author_c5.py), so it does not depend on
the parser under test. Traps added per loop: a stock question 2 s before the item changes (the answer must not be
said), a question 2.5 s into a new item about a size of the item before (must stay silent), and a price question
2 s before a price edit (the answer must carry the new price). No randomness: rerunning gives the same file.
"""

import importlib.util
import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
C5 = ROOT / "data/comments/c5"
OUT = HERE / "eval-c5.jsonl"
LOOPS = range(1, 6)
DRAFT_MS, LIVE_MS, FRAME_MS, SOLD_OUT_MS, LATE_MS = 1500, 4000, 1000, 8000, 300
EDIT_VND = 20000
DRAFT_ONLY = {"SP03": {1, 3, 5}}
NO_SKU = {"SP09", "SP10"}
POLICY = {
    "Mua 2 {u} có giá tốt hơn không shop", "Ship về Cần Thơ mất phí bao nhiêu", "Giá trên thẻ là giá cuối chưa shop",
    "Có freeship không shop, giá bao nhiêu", "Giá {u} này có kèm hộp không ạ", "Giá có bao ship chưa ạ",
    "Giá sỉ {u} này sao shop", "Tổng tiền 1 {u} cả ship là bao nhiêu",
}  # fmt: skip


def _author():
    spec = importlib.util.spec_from_file_location("author_c5", C5 / "author_c5.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _low(s: str) -> str:
    return " ".join(unicodedata.normalize("NFC", s).lower().split())


class Templates:
    """Which bank template a comment text came from (with the author's prefix/suffix variants)."""

    def __init__(self, a):
        pre = [v[1:] for k in a.VARIANTS["question"].values() for v in k if v.startswith("^")]
        suf = [v for k in a.VARIANTS["question"].values() for v in k if not v.startswith("^")]
        self.wrap = (
            "^(?:" + "|".join(re.escape(_low(p).rstrip()) + r"\s*" for p in pre) + ")?",
            "(?:" + "|".join(re.escape(_low(s).strip()) for s in suf) + ")?$",
        )
        self.banks = []
        for bank, kind in ((a.PRICE, "price"), (a.STOCK_SIZED, "stock"), (a.STOCK_ONE, "stock")):
            for t in bank:
                body = re.escape(a.norm(t)).replace(r"\{u\}", "(?:đôi|cái)").replace(r"\{s\}", r"(?P<s>\w+)")
                k = "policy" if t in POLICY else kind
                self.banks.append((re.compile(self.wrap[0] + body + r"\s*" + self.wrap[1]), k, t))
        self.norm = a.norm

    def match(self, text: str):
        t = self.norm(text)
        hits = {(k, (m.groupdict().get("s") or None)) for p, k, _ in self.banks if (m := p.match(t))}
        if len(hits) != 1:
            raise ValueError(f"{len(hits)} template matches for {text!r}: {hits}")
        kind, size = hits.pop()
        return kind, size.upper() if size else None


def _segment(windows, t_ms):
    for s in windows["segments"]:
        if s["from_ms"] <= t_ms < s["to_ms"]:
            return s
    return None


def _card(k, item, cat, version, status, price, t_ms, frame_ms, sold_out=(), edited_by=None):
    no_sku = item in NO_SKU
    variants = [] if no_sku else [
        {"size": v["size"], "color": v["color"], "stock": 0 if v["size"] in sold_out else v["stock"]}
        for v in cat["variants"]
    ]  # fmt: skip
    return {
        "event_id": f"card.k{k}.{item}.v{version}", "session_id": f"answers-eval-{k}", "ts_ms": t_ms,
        "source": "replay", "v": 1, "type": "card", "card_id": f"card-k{k}-{item}", "version": version,
        "sku": None if no_sku else item, "name": cat["name"], "price": price, "currency": "VND",
        "variants": variants, "stock": sum(v["stock"] for v in variants), "image_url": None, "status": status,
        "provenance": {"frame_ts_ms": frame_ms, "speech_event_ids": [], "edited_by": edited_by},
    }  # fmt: skip


def card_events(k, catalog, windows):
    """Card events of loop k: (t_ms, item, payload)."""
    out, last = [], {}
    for seg in windows["segments"]:
        item, start = seg["product"], seg["from_ms"]
        if item is None:
            continue
        cat, frame = catalog[item], start + FRAME_MS
        if item in last:  # shown again: same card, new frame
            p = dict(last[item], version=last[item]["version"] + 1, ts_ms=start + DRAFT_MS)
            p["provenance"] = dict(p["provenance"], frame_ts_ms=frame, edited_by=None)
            p["event_id"] = f"card.k{k}.{item}.v{p['version']}"
            out.append((start + DRAFT_MS, item, p))
            last[item] = p
            continue
        steps = [(start + DRAFT_MS, "draft")]
        if k not in DRAFT_ONLY.get(item, ()):
            steps.append((start + LIVE_MS, "live"))
            if k % 2 == 0 and item not in NO_SKU and cat["variants"][0]["size"] is not None:
                steps.append((start + SOLD_OUT_MS, "sold_out"))
            if item in windows["price_changes"]:
                steps.append((windows["price_changes"][item][1], "price"))
        sold_out, price, status, version, prev = (), cat["price"], "draft", 0, None
        for t, step in sorted(steps):
            version += 1
            if step == "draft":
                p = _card(k, item, cat, version, "draft", None, t, frame)
            else:
                if step == "sold_out":
                    sold_out = (cat["variants"][0]["size"],)
                if step == "price":
                    price -= EDIT_VND
                status = "live" if step == "live" else "edited"
                p = _card(
                    k,
                    item,
                    cat,
                    version,
                    status,
                    price,
                    t,
                    frame,
                    sold_out,
                    None if step == "live" else "seller",
                )
            out.append((t, item, p))
            if step in ("sold_out", "price") and prev is not None:
                out.append((t + LATE_MS, item, prev))  # the version before arrives again, late
            prev = p
        last[item] = prev
    return sorted(out, key=lambda e: (e[0], e[2]["version"]))


def gold_of(row, windows, tpl):
    seg = _segment(windows, row["ts_ms"])
    on_screen = seg["product"] if seg else None
    label, order = row["label"], row.get("order")
    if label in ("price_question", "stock_question"):
        kind, size = tpl.match(row["text"])
        assert on_screen is not None, row["comment_id"]
        return {"kind": kind, "size": size, "item": on_screen, "category": kind, "expect": "answer"}
    if label == "order":
        ref, item, size = order["reference_kind"], order["product"], order["size"]
        if ref == "current_card" and size is None and item and item not in NO_SKU and item < "SP07":
            assert item == on_screen, row["comment_id"]
            return {
                "kind": "ask_size",
                "size": None,
                "item": item,
                "category": "ask_size",
                "expect": "answer",
            }
        cat = {"previous_card": "order_previous", "unresolved": "order_unresolved"}.get(ref)
        cat = cat or ("order_sized" if size else "order_one_size")
        return {"kind": None, "size": size, "item": item, "category": cat, "expect": "silent"}
    return {"kind": None, "size": None, "item": None, "category": "not_a_question", "expect": "silent"}


def traps(k, catalog, windows):
    """Added comments per loop: (t_ms, comment_id, text, label, gold)."""
    out, n = [], 0
    segs = [s for s in windows["segments"]]
    for a, b in zip(segs, segs[1:], strict=False):
        if a["product"] is None or b["product"] is None:
            continue
        sizes = [v["size"] for v in catalog[a["product"]]["variants"] if v["size"] is not None]
        n += 1
        text = f"Shop ơi size {sizes[0]} còn không" if sizes else "Món này còn hàng không shop"
        gold = {"kind": "stock", "size": sizes[0] if sizes else None, "item": a["product"],
                "category": "trap_switch_before_answer", "expect": "silent"}  # fmt: skip
        out.append((b["from_ms"] - 2000, f"cx-{k}-{n:02d}", text, "stock_question", gold))
        if sizes:
            n += 1
            gold = {"kind": "stock", "size": sizes[-1], "item": a["product"], "category": "trap_right_after_switch",
                    "expect": "silent"}  # fmt: skip
            out.append((b["from_ms"] + 2500, f"cx-{k}-{n:02d}", f"Size {sizes[-1]} còn không shop", "stock_question",
                        gold))  # fmt: skip
    for item, times in sorted(windows["price_changes"].items()):
        if k in DRAFT_ONLY.get(item, ()):
            continue
        n += 1
        gold = {"kind": "price", "size": None, "item": item, "category": "trap_price_edit_before_answer",
                "expect": "answer"}  # fmt: skip
        out.append((times[1] - 2000, f"cx-{k}-{n:02d}", "Giá món này bao nhiêu shop", "price_question", gold))
    return out


def build():
    catalog = {c["sku"]: c for c in json.loads((C5 / "catalog.json").read_text())}
    windows = json.loads((C5 / "windows.json").read_text())
    tpl = Templates(_author())
    rows = []
    for k in LOOPS:
        loop = [(s["from_ms"], 0, {"type": "show", "item": s["product"]}) for s in windows["segments"]]
        loop += [
            (t, 1, {"type": "card", "item": item, "payload": p})
            for t, item, p in card_events(k, catalog, windows)
        ]
        for line in (C5 / f"stream-{k}.jsonl").read_text().splitlines():
            r = json.loads(line)
            gold = gold_of(r, windows, tpl)
            loop.append((r["ts_ms"], 2, {"type": "comment", "comment_id": r["comment_id"], "text": r["text"],
                                         "label": r["label"], "gold": gold}))  # fmt: skip
        for t, cid, text, label, gold in traps(k, catalog, windows):
            loop.append(
                (t, 2, {"type": "comment", "comment_id": cid, "text": text, "label": label, "gold": gold})
            )
        for t, _, ev in sorted(loop, key=lambda e: (e[0], e[1])):
            rows.append({"loop": k, "t_ms": t, **ev})
    return rows


def main(argv):
    rows = build()
    text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    if argv[:1] == ["--check"]:
        same = OUT.exists() and OUT.read_text() == text
        print("eval-c5.jsonl", "up to date" if same else "differs from the builder")
        return 0 if same else 1
    OUT.write_text(text)
    comments = [r for r in rows if r["type"] == "comment"]
    by = {}
    for r in comments:
        by[r["gold"]["category"]] = by.get(r["gold"]["category"], 0) + 1
    print(len(rows), "rows", len(comments), "comments", dict(sorted(by.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
