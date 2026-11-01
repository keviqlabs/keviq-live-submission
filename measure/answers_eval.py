"""Offline score of the answers about the item on screen: python -m measure.answers_eval [--json FILE]

Reads data/answers/eval-c5.jsonl (built by data/answers/build_eval.py), runs the selector of answers/ over each
loop, and judges what it would say with its own reading of the sentence against its own truth: the item really on
screen (the show rows, which the card stream sees 1.5 s late) and the highest card version of that item received so
far. It shares no parsing with answers/reply.py.

Per comment the outcome is correct (answered right, deferred to the seller while the fact asked for is not
approved, or silent where silence is expected), wrong (a price, count or size that differs from the approved card,
a sentence about another item or said after the item changed, a reply to a comment that is not a question, a
reply that does not answer) or no answer (with the selector's reason). Two runs: with the comment labels of the
set, and from the text alone.

The gate table plays every spoken sentence again as if audio started X ms after the decision: answers/reply.py's
check() against the cards received by then drops it or lets it through, and every sentence let through is judged
again at that moment. --rescore applies the same scorer to the size and stock cases of the 31 October paid rescore
(loop 1), with the cards that run had (a) and with an approved card with stock for every item (b).
"""

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from answers.facts import Board
from answers.reply import Answer, check
from answers.selector import Decision, replay

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "data/answers/eval-c5.jsonl"
C5 = ROOT / "data/comments/c5"
RESCORE = ROOT / "docs/evidence/2026-10-31/rescore-paid-1031-1615"
GATE_DELAYS_MS = (0, 300, 1000, 3000)
WRONG_FACT = ("wrong_price", "wrong_stock", "wrong_variant")
CATEGORIES = ("price", "policy", "stock", "ask_size", "trap_price_edit_before_answer", "trap_switch_before_answer",
              "trap_right_after_switch", "order_sized", "order_one_size", "order_previous", "order_unresolved",
              "not_a_question")  # fmt: skip


# --- truth -------------------------------------------------------------------------------------------------------


def _low(s: str) -> str:
    return " ".join(unicodedata.normalize("NFC", s).lower().split())


@dataclass(frozen=True)
class State:
    """What the seller approved for one item at one moment. sizes is None when no count may be said."""

    approved: bool
    price: int | None
    sizes: dict | None

    @classmethod
    def of(cls, card: dict | None) -> "State":
        if card is None:
            return cls(False, None, None)
        price = card.get("price")
        ok = card.get("status") in ("live", "edited") and type(price) is int and price > 0
        sizes = None
        if ok and card.get("sku") and card.get("variants"):
            sizes = {}
            for v in card["variants"]:
                key = str(v["size"]).upper() if v.get("size") is not None else None
                sizes[key] = sizes.get(key, 0) + v["stock"]
        return cls(ok, price if ok else None, sizes)

    @property
    def sized(self) -> bool:
        return self.sizes is not None and any(k is not None for k in self.sizes)


class Truth:
    """One loop: the item on screen at any time, and the approved state of any item at any time."""

    def __init__(self, rows: list[dict]):
        self.shows = sorted((r["t_ms"], r["item"]) for r in rows if r["type"] == "show")
        self.cards: dict[str, list] = defaultdict(list)
        self.item_of: dict[str, str] = {}
        self.names: dict[str, str] = {}
        for r in rows:
            if r["type"] == "card":
                self.cards[r["item"]].append((r["t_ms"], r["payload"]))
                self.item_of[r["payload"]["card_id"]] = r["item"]
                self.names[r["item"]] = _low(re.sub(r"^[A-Z]+\d+\s+", "", r["payload"]["name"]))

    def on_screen(self, t_ms: int) -> str | None:
        item = None
        for t, i in self.shows:
            if t > t_ms:
                break
            item = i
        return item

    def state(self, item: str | None, t_ms: int) -> State:
        seen = [p for t, p in self.cards.get(item, ()) if t <= t_ms]
        return State.of(max(seen, key=lambda p: p["version"]) if seen else None)

    def named(self, text: str) -> str | None:
        t = _low(text)
        hits = [i for i, n in self.names.items() if n and n in t]
        return max(hits, key=lambda i: len(self.names[i])) if hits else None


# --- reading a sentence ------------------------------------------------------------------------------------------

_MONEY = re.compile(r"(\d+) triệu(?: (\d+) nghìn)? đồng|(\d+) nghìn đồng|(\d{1,3}(?:\.\d{3})+) đồng")
_ASK = re.compile(r"bạn lấy size nào")
_LIST = re.compile(r"còn (size \w+(?:, size \w+)*)")
_PAIR = re.compile(r"size (\w+) (?:còn (\d+)|hết hàng)")
_TOTAL = re.compile(r"còn (\d+)")
_SOLD_OUT = re.compile(r"hết hàng")
_DEFER = re.compile(r"xác nhận|trả lời bạn")


@dataclass
class Reading:
    money: list
    pairs: dict
    totals: list
    sold_out: bool
    ask: list | None
    defer: bool
    stray_digits: bool

    @property
    def facts(self) -> bool:
        return bool(self.money or self.pairs or self.totals or self.sold_out or self.ask)


def read(text: str, names=()) -> Reading:
    t = _low(text)
    for n in names:
        t = t.replace(n, " ")
    money = []
    for m in _MONEY.finditer(t):
        if m[1]:
            money.append(int(m[1]) * 1_000_000 + int(m[2] or 0) * 1000)
        elif m[3]:
            money.append(int(m[3]) * 1000)
        else:
            money.append(int(m[4].replace(".", "")))
    t = _MONEY.sub(" ", t)
    ask = None
    if _ASK.search(t):
        m = _LIST.search(t)
        ask = [s.split()[1].upper() for s in m[1].split(", ")] if m else []
        t = _LIST.sub(" ", t) if m else t
    pairs = {m[1].upper(): int(m[2]) if m[2] else 0 for m in _PAIR.finditer(t)}
    t = _PAIR.sub(" ", t)
    totals = [int(n) for n in _TOTAL.findall(t)]
    t = _TOTAL.sub(" ", t)
    sold_out = bool(_SOLD_OUT.search(t))
    return Reading(money, pairs, totals, sold_out, ask, bool(_DEFER.search(t)), any(c.isdigit() for c in t))


def wrong_fact(r: Reading, s: State) -> str | None:
    """The first said fact that differs from the approved state, or None."""
    if r.stray_digits:
        return "unexplained_number"
    if any(not s.approved or v != s.price for v in r.money):
        return "wrong_price"
    said = dict(r.pairs)
    if r.ask is not None:
        said.update({k: None for k in r.ask})
    if (said or r.totals or r.sold_out) and s.sizes is None:
        return "wrong_stock"
    for size, n in said.items():
        if size not in s.sizes:
            return "wrong_variant"
        if n is not None and n != s.sizes[size]:
            return "wrong_stock"
    if r.ask is not None and set(r.ask) != {k for k, n in s.sizes.items() if n > 0}:
        return "wrong_stock"
    total = sum(s.sizes.values()) if s.sizes else 0
    if any(n != total for n in r.totals) or (r.sold_out and not r.pairs and total != 0):
        return "wrong_stock"
    return None


def known(kind: str | None, size: str | None, s: State) -> bool:
    """Whether the card approves the fact asked for (when not, deferring or staying silent is right)."""
    if kind == "price":
        return s.approved
    if kind == "stock":
        return s.sizes is not None and (size is None or not s.sized or size in s.sizes)
    if kind == "ask_size":
        return s.sized
    return False


# --- judging -----------------------------------------------------------------------------------------------------


def judge_sentence(d: Decision, truth: Truth, t_ms: int) -> tuple[str, str | None, Reading]:
    """(item the sentence is about, what is wrong with it at t_ms or None, reading)."""
    item = truth.named(d.text) or truth.item_of.get(d.card_id)
    r = read(d.text, truth.names.values())
    if item != truth.on_screen(t_ms):
        return item, "after_switch", r
    return item, wrong_fact(r, truth.state(item, t_ms)), r


def judge(gold: dict, d: Decision | None, truth: Truth, asked_ms: int) -> tuple[str, str]:
    """(correct | wrong | no_answer, detail) of one comment; d is the decision that holds it, if any."""
    kind, size = gold["kind"], gold["size"]
    if d is None or d.action == "silent":
        if gold["expect"] == "silent":
            return "correct", "silent"
        t = d.ts_ms if d is not None else asked_ms
        if kind == "ask_size" and not known(kind, size, truth.state(gold["item"], t)):
            return "correct", "silent_not_known"
        return "no_answer", d.reason if d is not None else "not_parsed"
    if kind is None:
        return "wrong", "spoke_on_negative"
    item, wrong, r = judge_sentence(d, truth, d.ts_ms)
    if item != gold["item"]:
        return "wrong", "wrong_item"
    if wrong:
        return "wrong", wrong
    s = truth.state(item, d.ts_ms)
    if not r.facts:
        if not r.defer:
            return "wrong", "off_topic"
        return ("no_answer", "deferred_known") if known(kind, size, s) else ("correct", "deferred")
    if kind == "price" and r.money:
        return "correct", "answered"
    if kind == "stock" and (r.pairs or r.totals or r.sold_out):
        if size is not None and s.sized and size not in r.pairs:
            return "wrong", "wrong_variant"
        return "correct", "answered"
    if kind == "ask_size" and (r.ask or r.sold_out):
        return "correct", "answered"
    return "wrong", "off_topic"


def events_of(rows: list[dict], labels: bool) -> list[dict]:
    out = []
    for r in rows:
        if r["type"] == "card":
            out.append({"t_ms": r["t_ms"], "type": "card", "payload": r["payload"]})
        elif r["type"] == "comment":
            out.append({"t_ms": r["t_ms"], "type": "comment", "comment_id": r["comment_id"], "text": r["text"],
                        "label": r["label"] if labels else None})  # fmt: skip
    return out


def run_loop(rows: list[dict], labels: bool) -> tuple[list[Decision], list[dict]]:
    """Decisions of one loop and one result row per comment."""
    truth = Truth(rows)
    decisions = replay(events_of(rows, labels))
    held = {cid: d for d in decisions for cid in d.comment_ids}
    out = []
    for r in rows:
        if r["type"] != "comment":
            continue
        d = held.get(r["comment_id"])
        outcome, detail = judge(r["gold"], d, truth, r["t_ms"])
        out.append({"comment_id": r["comment_id"], "category": r["gold"]["category"], "outcome": outcome,
                    "detail": detail, "said": d.text if d is not None else "",
                    "wait_ms": d.ts_ms - r["t_ms"] if d is not None and d.action != "silent" else None})  # fmt: skip
    return decisions, out


def gate(rows: list[dict], decisions: list[Decision]) -> dict:
    """Every spoken sentence, as if audio started delay ms after the decision."""
    truth = Truth(rows)
    cards = sorted((r["t_ms"], r["payload"]["version"], r["payload"]) for r in rows if r["type"] == "card")
    out = {}
    spoken = [d for d in decisions if d.action != "silent"]
    for delay in GATE_DELAYS_MS:
        dropped, passed, wrong = Counter(), 0, Counter()
        for d in spoken:
            t = d.ts_ms + delay
            board = Board()
            for ts, _, p in cards:
                if ts > t:
                    break
                board.ingest(p, ts)
            reason = check(Answer(d.action, d.text, d.card_id, d.version, d.kind, d.reason), board.current())
            if reason:
                dropped[reason] += 1
                continue
            passed += 1
            _, w, _ = judge_sentence(d, truth, t)
            if w:
                wrong[w] += 1
        out[delay] = {
            "spoken": len(spoken),
            "dropped": dict(dropped),
            "passed": passed,
            "passed_wrong": dict(wrong),
        }
    return out


def load(path: Path = EVAL) -> dict[int, list[dict]]:
    loops: dict[int, list[dict]] = defaultdict(list)
    for line in path.read_text().splitlines():
        r = json.loads(line)
        loops[r["loop"]].append(r)
    return dict(sorted(loops.items()))


def score(loops: dict[int, list[dict]], labels: bool) -> dict:
    results, decisions, gates = [], [], defaultdict(lambda: {"spoken": 0, "dropped": Counter(), "passed": 0,
                                                             "passed_wrong": Counter()})  # fmt: skip
    for rows in loops.values():
        ds, res = run_loop(rows, labels)
        results += res
        decisions += ds
        for delay, g in gate(rows, ds).items():
            a = gates[delay]
            a["spoken"] += g["spoken"]
            a["passed"] += g["passed"]
            a["dropped"].update(g["dropped"])
            a["passed_wrong"].update(g["passed_wrong"])
    spoken = [d for d in decisions if d.action != "silent"]
    return {
        "results": results,
        "sentences": {"spoken": len(spoken), "by_action": dict(Counter(d.action for d in spoken))},
        "gate": {k: {**v, "dropped": dict(v["dropped"]), "passed_wrong": dict(v["passed_wrong"])}
                 for k, v in sorted(gates.items())},
    }  # fmt: skip


# --- rescore of the 31 October paid run --------------------------------------------------------------------------


def _rescore_cards(k: int, sku_items: set[str]) -> list[dict]:
    catalog = {c["sku"]: c for c in json.loads((C5 / "catalog.json").read_text())}
    windows = json.loads((C5 / "windows.json").read_text())
    rows, version = [], {}
    for seg in windows["segments"]:
        item = seg["product"]
        if item is None:
            continue
        cat, frame = catalog[item], seg["from_ms"] + 1000
        has = item in sku_items
        variants = (
            [{"size": v["size"], "color": v["color"], "stock": v["stock"]} for v in cat["variants"]]
            if has
            else []
        )
        steps = [(1500, "live")] if item in version else [(1500, "draft"), (4000, "live")]
        for dt, status in steps:
            version[item] = version.get(item, 0) + 1
            p = {"card_id": f"card-r{k}-{item}", "version": version[item], "sku": item if has else None,
                 "name": cat["name"], "price": None if status == "draft" else cat["price"], "currency": "VND",
                 "variants": variants, "stock": sum(v["stock"] for v in variants), "status": status,
                 "provenance": {"frame_ts_ms": frame, "speech_event_ids": [], "edited_by": None}}  # fmt: skip
            rows.append({"loop": k, "t_ms": seg["from_ms"] + dt, "type": "card", "item": item, "payload": p})
    rows += [
        {"loop": k, "t_ms": s["from_ms"], "type": "show", "item": s["product"]} for s in windows["segments"]
    ]
    return rows


def rescore(loops: dict[int, list[dict]], evidence: Path = RESCORE) -> dict:
    """Loop 1 with the run's labels, against the cards of mode a (the run) and mode b (all with stock)."""
    run = json.loads(evidence.with_suffix(".json").read_text())["score"]
    labels = {
        r["comment_id"]: r["label"]
        for r in map(json.loads, open(f"{evidence}-comments.jsonl"))
        if r["loop"] == 1
    }
    with_sku = {v for v in run["card_sku"].values() if v}
    stream = {r["comment_id"]: r for r in map(json.loads, (C5 / "stream-1.jsonl").read_text().splitlines())}
    size_ids = sorted(c for c, r in stream.items() if r["label"] == "order" and r["order"]["reference_kind"] ==
                      "current_card" and r["order"]["size"] is None and r["order"]["product"] < "SP07")  # fmt: skip
    stock_ids = sorted(c for c, r in stream.items() if r["label"] == "stock_question")
    comments = [dict(r, label=labels.get(r["comment_id"], r["label"])) for r in loops[1]
                if r["type"] == "comment" and r["comment_id"].startswith("c5-")]  # fmt: skip
    out = {"run_cards_with_sku": sorted(with_sku), "size_ids": size_ids, "stock_ids": stock_ids}
    for mode, items in (("a", with_sku), ("b", {f"SP{i:02d}" for i in range(1, 11)})):
        rows = sorted(_rescore_cards(1, items) + comments, key=lambda r: (r["t_ms"], r["type"] != "show"))
        _, res = run_loop(rows, labels=True)
        by = {r["comment_id"]: r for r in res}
        out[mode] = {
            "size": {c: (by[c]["outcome"], by[c]["detail"], by[c]["said"]) for c in size_ids},
            "stock": {c: (by[c]["outcome"], by[c]["detail"], by[c]["said"]) for c in stock_ids},
        }
    return out


# --- printing ----------------------------------------------------------------------------------------------------


def table(results: list[dict]) -> str:
    by = defaultdict(list)
    for r in results:
        by[r["category"]].append(r)
    lines = ["| group | n | correct | answered | deferred | silent ok | wrong | no answer | wrong / no-answer detail |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---|"]  # fmt: skip
    total = Counter()
    for cat in [c for c in CATEGORIES if c in by] + sorted(set(by) - set(CATEGORIES)):
        rs = by[cat]
        c = Counter(r["outcome"] for r in rs)
        d = Counter(r["detail"] for r in rs)
        other = Counter(r["detail"] for r in rs if r["outcome"] != "correct")
        row = (len(rs), c["correct"], d["answered"], d["deferred"], d["silent"] + d["silent_not_known"], c["wrong"],
               c["no_answer"])  # fmt: skip
        total.update(
            dict(zip(("n", "correct", "answered", "deferred", "silent", "wrong", "no"), row, strict=True))
        )
        detail = ", ".join(f"{k} {v}" for k, v in sorted(other.items())) or "-"
        lines.append(f"| {cat} | " + " | ".join(map(str, row)) + f" | {detail} |")
    t = total
    lines.append(f"| **all** | {t['n']} | {t['correct']} | {t['answered']} | {t['deferred']} | {t['silent']} | "
                 f"{t['wrong']} | {t['no']} | |")  # fmt: skip
    return "\n".join(lines)


def gate_table(g: dict) -> str:
    lines = [
        "| audio starts after | spoken | dropped by the check | passed | passed but wrong |",
        "|---|---:|---|---:|---|",
    ]
    for delay, v in g.items():
        dropped = ", ".join(f"{k} {n}" for k, n in sorted(v["dropped"].items())) or "0"
        wrong = ", ".join(f"{k} {n}" for k, n in sorted(v["passed_wrong"].items())) or "0"
        lines.append(f"| {delay} ms | {v['spoken']} | {dropped} | {v['passed']} | {wrong} |")
    return "\n".join(lines)


def wrong_facts(results: list[dict]) -> Counter:
    return Counter(r["detail"] for r in results if r["outcome"] == "wrong" and r["detail"] in WRONG_FACT)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="write every result to this file")
    ap.add_argument("--rescore", action="store_true", help="also score the 31 October paid rescore cases")
    a = ap.parse_args(argv)
    loops = load()
    n = sum(1 for rows in loops.values() for r in rows if r["type"] == "comment")
    report = {}
    for name, labels in (("labels", True), ("text_only", False)):
        s = score(loops, labels)
        report[name] = s
        print(f"\n## {name}: {n} comments in {len(loops)} loops, {s['sentences']['spoken']} sentences said "
              f"{s['sentences']['by_action']}\n")  # fmt: skip
        print(table(s["results"]))
        print(f"\nwrong price / stock / variant: {dict(wrong_facts(s['results'])) or 0}\n")
        print(gate_table(s["gate"]))
    if a.rescore:
        r = rescore(loops)
        report["rescore"] = r
        print(
            f"\n## rescore-paid-1031-1615, loop 1 (cards with SKU in that run: {', '.join(r['run_cards_with_sku'])})"
        )
        for mode in ("a", "b"):
            for group in ("size", "stock"):
                cases = r[mode][group]
                c = Counter(v[1] if v[0] == "correct" else v[0] for v in cases.values())
                print(f"mode {mode} {group}: answered {c['answered']}/{len(cases)}, deferred {c['deferred']}, "
                      f"silent (not known) {c['silent_not_known']}, wrong {c['wrong']}, no answer {c['no_answer']}")  # fmt: skip
            for cid, (outcome, detail, said) in {**r[mode]["size"], **r[mode]["stock"]}.items():
                print(f"  {mode} {cid} {outcome} {detail} | {said}")
    if a.json:
        Path(a.json).write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True, default=str))
    bad = sum(wrong_facts(report[k]["results"]).total() for k in ("labels", "text_only"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
