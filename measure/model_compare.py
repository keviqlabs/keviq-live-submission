"""Capped model comparison for product identity on test1 frames (a measurement, not a production change).

    python -m measure.model_compare manifest --gold ../keviq-live-event/data/local/test1   # freeze, then commit
    python -m measure.model_compare run --phase spark --gold ../keviq-live-event/data/local/test1
    python -m measure.model_compare run --phase wonder --gold ../keviq-live-event/data/local/test1
    python -m measure.model_compare score

Three schemas on the same frames: A = production SCHEMA and prompt (worker/vision/spark.py, last=None);
B0 = kind + colour with an explicit 'unclear', no item list; B = B0 + the session's item list and an item code
(SP01..SP10, unsure, none). Configs: Muse Spark minimal, Muse Spark high, Muse Wonder high.

Every call goes through MetaClient, so every POST is checked by the meter first and logged as a model_call by
`_log`. The meter here is CapMeter: before a call it holds the worst case of the call (two POSTs: the call and one
schema retry), before each POST the worst case of that POST, and it releases the hold once the POST is logged.
A POST whose cost is unknown (no reply) keeps its worst case as spent. The model_call events go to a JSONL file
through `make_event` (contract-validated), not to the project database.

Muse Wonder is BLOCKED in meta/pricing.py and has no price there. Inside `wonder_allowed()` only, this process
rebinds meta.client's BLOCKED (without Wonder) and cost_usd (Wonder at 4.00 USD in / 20.00 USD out per million
tokens, cached input charged as full input), and restores both on exit. meta/pricing.py is not changed.
"""

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

import meta.client as mc
from app.contracts import make_event, now_ms
from measure import model_compare_score as sc
from meta import pricing
from meta.client import MetaClient, ModelError, SchemaError
from meta.meter import BudgetExceeded
from worker.brain.card import COLORS
from worker.vision import frames as frames_mod
from worker.vision import spark

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "evidence" / "2026-11-01"
MANIFEST, ROWS, LEDGER, SCORE = (
    "model-compare-manifest.json",
    "model-compare-calls.jsonl",
    "model-compare-model-calls.jsonl",
    "model-compare-score.json",
)
CATALOG_FILE = ROOT / "data" / "comments" / "c5" / "catalog.json"
COMMENTS_FILE = ROOT / "data" / "comments" / "c5" / "stream-1.jsonl"
RUN_1615 = ROOT / "docs" / "evidence" / "2026-10-31" / "rescore-paid-1031-1615.json"
SESSION = "model-compare-1101"
PURPOSE = "model_compare"

SPARK, WONDER = "muse-spark-1.3", "muse-wonder-1.0"
WONDER_PRICE = {"input_per_m": 4.00, "output_per_m": 20.00}  # cached input price not published: full input
CONFIGS = {
    "spark-minimal": (SPARK, "minimal"),
    "spark-high": (SPARK, "high"),
    "wonder-high": (WONDER, "high"),
}
PHASES = {
    "spark": {
        "cap": 2.50,
        "arms": [(c, s) for c in ("spark-minimal", "spark-high") for s in ("A", "B0", "B")],
    },
    "wonder": {"cap": 5.00, "arms": [("wonder-high", "B"), ("wonder-high", "B0")]},
}
TOTAL_CAP = 5.00
OUT_FLOOR = {"spark-minimal": 1000, "spark-high": 8000, "wonder-high": 8000}
IN_FLOOR = 1500
STOP_AFTER_ERRORS = 5

CATALOG: list[dict] = json.loads(CATALOG_FILE.read_text())
KINDS = [*dict.fromkeys(i["kind"] for i in CATALOG), "khác", "none"]

COMMON = (
    "Đây là một khung hình từ livestream bán hàng. Món người bán đang cầm hoặc giới thiệu là gì? "
    "state: 'one_product' khi người bán đang cầm hoặc giới thiệu một món và nhìn rõ đó là món gì; "
    "'no_product' khi người bán không cầm và không giới thiệu món nào; 'unclear' khi không chắc (món bị che, "
    "mờ, đang đổi món, hoặc thấy nhiều món). Không chắc thì chọn 'unclear', đừng đoán. "
    "kind: loại món, chọn 'giày', 'áo', 'tay cầm' (tay cầm chơi game) hoặc 'bàn phím'; món khác thì 'khác'; "
    "không có món thì 'none'. Quần áo người bán đang mặc trên người không phải sản phẩm đang giới thiệu. "
    f"color: tiếng Việt thường, chọn trong {COLORS} nếu khớp; không rõ thì null. "
)
PROMPT_B0 = COMMON + "confidence: mức chắc chắn 0..1."
PROMPT_B = (
    COMMON
    + "Các món bán trong phiên (mã: loại, màu): "
    + "; ".join(f"{i['sku']}: {i['kind']}, {sc.colour_of(i)}" for i in CATALOG)
    + ". sku: mã của món đang giới thiệu, chỉ khi cả loại và màu khớp đúng một mã trong danh sách và bạn chắc "
    "chắn; không chắc thì 'unsure'; không có món thì 'none'. Chọn 'unsure' tốt hơn chọn sai mã. "
    "confidence: mức chắc chắn 0..1."
)
_IDENTITY = {
    "state": {"type": "string", "enum": ["one_product", "no_product", "unclear"]},
    "kind": {"type": "string", "enum": KINDS},
    "color": {"type": ["string", "null"]},
}
_CONF = {"confidence": {"type": "number", "minimum": 0, "maximum": 1}}
SCHEMAS = {
    "A": spark.SCHEMA,
    "B0": {
        "type": "object",
        "additionalProperties": False,
        "required": ["state", "kind", "color", "confidence"],
        "properties": {**_IDENTITY, **_CONF},
    },
    "B": {
        "type": "object",
        "additionalProperties": False,
        "required": ["state", "kind", "color", "sku", "confidence"],
        "properties": {**_IDENTITY, "sku": {"type": "string", "enum": [*sc.SKUS, "unsure", "none"]}, **_CONF},
    },
}


def frame_input(schema: str, jpeg: bytes) -> list[dict]:
    """A: the production prompt with no previous frame (never gold). B0 / B: the identity prompts."""
    if schema == "A":
        return spark.frame_input(jpeg, None)
    text = PROMPT_B0 if schema == "B0" else PROMPT_B
    return [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": text},
                {"type": "input_image", "image_url": frames_mod.data_url(jpeg)},
            ],
        }
    ]


# --- Muse Wonder: allowed and priced inside this experiment only -------------------------------------------


def experiment_cost(model, tokens_in=0, tokens_out=0, cached_in=0, units=None) -> float:
    if model == WONDER:
        return (tokens_in * WONDER_PRICE["input_per_m"] + tokens_out * WONDER_PRICE["output_per_m"]) / 1e6
    return pricing.cost_usd(model, tokens_in, tokens_out, cached_in, units)


@contextmanager
def wonder_allowed():
    saved = mc.BLOCKED, mc.cost_usd
    mc.BLOCKED = {k: v for k, v in saved[0].items() if k != WONDER}
    mc.cost_usd = experiment_cost
    try:
        yield
    finally:
        mc.BLOCKED, mc.cost_usd = saved


def prices(model: str) -> tuple[float, float]:
    p = WONDER_PRICE if model == WONDER else pricing.PRICES[model]
    return p["input_per_m"], p["output_per_m"]


# --- cap -----------------------------------------------------------------------------------------------------


@dataclass
class Call:
    config: str
    w: float  # worst case of one POST
    held: float = 0.0
    events: list = field(default_factory=list)


CURRENT: ContextVar[Call | None] = ContextVar("model_compare_call", default=None)


class Budget:
    """spent (logged costs) + unknown (worst case of POSTs that got no reply) + held (worst case of the POSTs
    in flight or about to be sent) never goes over the cap."""

    def __init__(self, cap: float, spent: float = 0.0, unknown: float = 0.0):
        self.cap, self.spent, self.unknown, self.held = cap, spent, unknown, 0.0

    def total(self) -> float:
        return self.spent + self.unknown + self.held

    def hold(self, amount: float) -> bool:
        if self.total() + amount > self.cap + 1e-12:
            return False
        self.held += amount
        return True

    def release(self, amount: float) -> None:
        self.held = max(self.held - amount, 0.0)


class CapMeter:
    """The meter MetaClient checks before every POST: the POST must be covered by a hold, or one is taken."""

    def __init__(self, budget: Budget):
        self.budget = budget

    async def check(self, session_id=None) -> None:
        call = CURRENT.get()
        need = call.w - call.held if call else float("inf")
        if need > 0:
            if call is None or not self.budget.hold(need):
                raise BudgetExceeded("experiment_cap", self.budget.total(), self.budget.cap)
            call.held += need


def no_reply(event: dict) -> bool:
    return bool(event["error"]) and (event["error"].startswith("transport_") or event["error"] == "cancelled")


class Ledger:
    """The `emit` of MetaClient: each model_call becomes a contract event appended to a JSONL file."""

    def __init__(self, path: Path, budget: Budget, session: str = SESSION):
        self.path, self.budget, self.session = path, budget, session

    async def __call__(self, fields: dict) -> None:
        event = make_event("model_call", self.session, now_ms(), "replay", **fields)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.budget.spent += event["cost_usd"]
        call = CURRENT.get()
        if call is None:
            return
        call.events.append(event)
        if event["error"] == "experiment_cap":  # refused before the POST: nothing was held for it
            return
        free = min(call.w, call.held)
        call.held -= free
        self.budget.release(free)
        if no_reply(event):
            self.budget.unknown += call.w


# --- run -----------------------------------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class Runner:
    def __init__(self, transport, out: Path, read_frame, cap: float, configs: dict | None = None):
        self.out, self.read_frame = out, read_frame
        self.configs = dict(configs or CONFIGS)
        self.rows_path, self.ledger_path = out / ROWS, out / LEDGER
        rows, ledger = read_jsonl(self.rows_path), read_jsonl(self.ledger_path)
        self.seen = {c: {"in": 0, "out": 0} for c in self.configs}
        for r in rows:
            if r["ok"] and r["config"] in self.seen:
                s = self.seen[r["config"]]
                s["in"], s["out"] = max(s["in"], r["max_post_in"]), max(s["out"], r["max_post_out"])
        spent = sum(e["cost_usd"] for e in ledger)
        unknown = sum(
            max(self.w_post(c) for c, (m, _) in self.configs.items() if m == e["model"])
            for e in ledger
            if no_reply(e)
        )
        self.budget = Budget(min(cap, TOTAL_CAP), spent, unknown)
        self.client = MetaClient(
            transport, CapMeter(self.budget), Ledger(self.ledger_path, self.budget), session_id=SESSION
        )
        self.done = {(r["t"], r["config"], r["schema"]) for r in rows}
        self.errors_in_a_row, self.stopped = 0, None

    def w_post(self, config: str) -> float:
        model = self.configs[config][0]
        p_in, p_out = prices(model)
        s = self.seen[config]
        return (max(IN_FLOOR, 1.2 * s["in"]) * p_in + max(OUT_FLOOR[config], 2 * s["out"]) * p_out) / 1e6

    async def one(self, frame: dict, config: str, schema: str) -> dict | None:
        """One call. None when the cap cannot cover its worst case (the run stops)."""
        model, effort = self.configs[config]
        call = Call(config, self.w_post(config))
        if not self.budget.hold(2 * call.w):
            self.stopped = (
                f"cap: spent {self.budget.spent:.4f} + unknown {self.budget.unknown:.4f} + held "
                f"{self.budget.held:.4f} + next call worst case {2 * call.w:.4f} > {self.budget.cap} USD"
            )
            return None
        call.held = 2 * call.w
        token = CURRENT.set(call)
        data, raw, error = None, None, None
        started = time.perf_counter()
        try:
            res = await self.client.responses(
                frame_input(schema, self.read_frame(frame)),
                SCHEMAS[schema],
                purpose=PURPOSE,
                model=model,
                name=f"product_{schema}",
                reasoning=effort,
            )
            data, raw = res.data, res.raw
        except SchemaError:
            error = "schema_failed_twice"
        except BudgetExceeded as exc:
            error = "experiment_cap"
            self.stopped = f"cap at a POST: {exc}"
        except ModelError as exc:
            error = str(exc)[:300]
        except Exception as exc:  # no reply: logged by the client with cost 0, kept as unknown here
            error = f"transport_{type(exc).__name__}"
        finally:
            wall_ms = round((time.perf_counter() - started) * 1000)
            self.budget.release(call.held)
            call.held = 0.0
            CURRENT.reset(token)
        posts = [e for e in call.events if e["error"] != "experiment_cap"]
        ok_posts = [e for e in posts if e["ok"]]
        details = ((raw or {}).get("usage") or {}).get("output_tokens_details") or {}
        row = {
            "t": frame["t"],
            "config": config,
            "model": model,
            "effort": effort,
            "schema": schema,
            "ok": data is not None,
            "error": error,
            "data": data,
            "posts": len(posts),
            "tokens_in": sum(e["tokens_in"] for e in posts),
            "tokens_out": sum(e["tokens_out"] for e in posts),
            "cached_in": ((raw or {}).get("usage") or {})
            .get("input_tokens_details", {})
            .get("cached_tokens", 0),
            "reasoning_tokens_last": details.get("reasoning_tokens"),
            "max_post_in": max((e["tokens_in"] for e in ok_posts), default=0),
            "max_post_out": max((e["tokens_out"] for e in ok_posts), default=0),
            "cost_usd": round(sum(e["cost_usd"] for e in posts), 8),
            "unknown_cost_posts": sum(no_reply(e) for e in posts),
            "latency_ms": wall_ms,
            "ledger_latency_ms": sum(e["latency_ms"] for e in posts),
            "event_ids": [e["event_id"] for e in posts],
        }
        s = self.seen[config]
        s["in"], s["out"] = max(s["in"], row["max_post_in"]), max(s["out"], row["max_post_out"])
        if (
            error
            and model == WONDER
            and effort
            and "HTTP 400" in error
            and "reasoning" in error.lower()
            and not self.stopped
        ):
            # pre-registered fallback: Wonder refuses the effort parameter -> the model's default, said in NOTE
            self.write(row | {"note": "refused effort; retried without it"})
            self.configs[config] = (model, None)
            return await self.one(frame, config, schema)
        self.write(row)
        return row

    def write(self, row: dict) -> None:
        with self.rows_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    async def run(self, plan: list[tuple[dict, str, str]], concurrency: int = 1, log=print) -> dict:
        todo = [p for p in plan if (p[0]["t"], p[1], p[2]) not in self.done]
        sem, n = asyncio.Semaphore(concurrency), 0
        start_spent = self.budget.spent

        async def go(item):
            nonlocal n
            async with sem:
                if self.stopped:
                    return
                row = await self.one(*item)
                if row is None:
                    return
                n += 1
                self.errors_in_a_row = self.errors_in_a_row + 1 if row["error"] else 0
                if self.errors_in_a_row >= STOP_AFTER_ERRORS:
                    self.stopped = f"{STOP_AFTER_ERRORS} failed calls in a row (last: {row['error']})"
                log(
                    f"{n}/{len(todo)} t={row['t']} {row['config']}/{row['schema']} "
                    f"{'ok' if row['ok'] else row['error']} {row['cost_usd']:.5f} USD {row['latency_ms']} ms "
                    f"spent={self.budget.spent:.4f}"
                )

        await asyncio.gather(*(go(p) for p in todo))
        return {
            "planned": len(plan),
            "already_done": len(plan) - len(todo),
            "called": n,
            "stopped": self.stopped,
            "spent_before": round(start_spent, 6),
            "spent_after": round(self.budget.spent, 6),
            "unknown_reserve": round(self.budget.unknown, 6),
        }


def plan_for(phase: str, frames: list[dict]) -> list[tuple[dict, str, str]]:
    """spark: frame by frame, all six Spark arms per frame. wonder: B on every frame, then B0 on every frame."""
    arms = PHASES[phase]["arms"]
    if phase == "spark":
        return [(f, c, s) for f in frames for c, s in arms]
    return [(f, c, s) for c, s in arms for f in frames]


# --- manifest ------------------------------------------------------------------------------------------------


def no_card_comments() -> list[dict]:
    """The 13 order comments of run 1615 that got no card (rescore-paid-1031-1615.json)."""
    run = json.loads(RUN_1615.read_text())
    ids = [w["comment_id"] for w in run["score"]["references"]["wrong"] if w["why"] == "no_card"]
    rows = {c["comment_id"]: c for c in read_jsonl(COMMENTS_FILE)}
    return [{k: rows[i][k] for k in ("comment_id", "ts_ms", "text", "order", "expected_action")} for i in ids]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def build_manifest(gold: Path) -> dict:
    labels = json.loads((gold / "labels.json").read_text())
    comments = no_card_comments()
    frames = sc.select(labels, CATALOG, comments)
    for f in frames:
        f["jpeg_sha256"] = hashlib.sha256((gold / "frames" / f["frame"]).read_bytes()).hexdigest()
    prompt_a = spark.frame_input(b"", None)[0]["content"][0]["text"]
    return {
        "session_id": SESSION,
        "code_commit": git("rev-parse", "HEAD"),
        "gold": "data/local/test1/labels.json (local, not in git); frames f_{t:05d}.jpg 360x640",
        "catalog": "data/comments/c5/catalog.json",
        "category_map": labels["category_map"],
        "price_changes": [
            {"sku": p["product"], "from_s": p["said_s"][0], "to_s": p["said_s"][1], "new_price": p["price"]}
            for p in labels["price_changes"]
        ],
        "no_card_comments": comments,
        "selection": (
            "Clear: 4 non-boundary seconds per item, evenly spaced over its non-boundary seconds. Boundary: the "
            "second nearest each product change's cue (boundary=True). No product: 6 non-boundary empty seconds, "
            "evenly spaced. Order comments: the comment's second (current card) or the last clear second of the "
            "gold item before the comment (previous card), tagged with the comment id. Strata: "
            "single_unique_colour (SP03 xám, SP06 xanh), same_colour_fashion (SP01, SP02, SP04, SP05: đen/trắng "
            "shared by shoes and shirts), same_colour_other (SP07-SP10: production can only call them other), "
            "boundary, no_product. Run order interleaves the groups so a prefix stays balanced."
        ),
        "configs": {k: {"model": m, "effort": e} for k, (m, e) in CONFIGS.items()},
        "wonder_effort_note": (
            "Wonder runs at effort high, the same as the Spark high arm, so the two differ by model only; "
            "fallback if the effort parameter is refused (HTTP 400): the model's default, said in the NOTE."
        ),
        "schemas": SCHEMAS,
        "prompts": {
            "A": prompt_a,
            "A_note": "worker/vision/spark.py frame_input(jpeg, None): no previous frame, never gold",
            "B0": PROMPT_B0,
            "B": PROMPT_B,
        },
        "prompt_sha": {"A": sha(prompt_a), "B0": sha(PROMPT_B0), "B": sha(PROMPT_B)},
        "phases": {
            "spark": "six Spark arms (2 efforts x A, B0, B), frame by frame in run order; cap 2.50 USD",
            "wonder": (
                "wonder-high on B for every frame, then B0 with what is left; total cap 5.00 USD for all arms. "
                "A is not run on Wonder: A cannot name SP07/SP09 or SP08/SP10 apart whatever the model sees."
            ),
        },
        "cap_rule": (
            "Before a call: spent + unknown + held + 2 x worst-case POST <= cap, else stop. Before every POST "
            "(meter check): that POST is held. A logged POST releases its hold; a POST with no reply keeps its "
            "worst case as unknown. Worst-case POST = max(1500, 1.2 x max tokens_in seen) x input price + "
            "max(floor, 2 x max tokens_out seen) x output price; floors: spark-minimal 1000, spark-high 8000, "
            "wonder 8000. The client sends no output limit, so the worst case is an estimate, not a bound."
        ),
        "scoring": {
            "A": "has_product false or category none -> abstain; else production match(category, colour) on the "
            "catalog: one item -> pick; several -> cannot_name (collision); none -> cannot_name (no_match)",
            "B0": "state not one_product, or kind khác/none -> abstain; else the one item of that kind and colour",
            "B": "sku SP01..SP10 -> pick (whatever the state); unsure / none -> abstain",
            "verdict": "clear: pick == gold correct, other pick wrong; boundary: pick of either side of the change "
            "correct, else wrong; no_product: any pick wrong; abstain, cannot_name and error are counted apart",
            "gated": "production gate shown apart: a pick with confidence < 0.5 becomes an abstain",
            "best": sc.BEST_RULE,
        },
        "frames": frames,
    }


def committed(path: Path) -> bool:
    rel = str(path.relative_to(ROOT))
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT, capture_output=True)
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=ROOT)
    return tracked.returncode == 0 and clean.returncode == 0


def check_manifest(man: dict, gold: Path) -> None:
    """The frames are the frozen ones: same selection from the labels, same image bytes."""
    labels = json.loads((gold / "labels.json").read_text())
    again = sc.select(labels, CATALOG, man["no_card_comments"])
    frozen = [{k: v for k, v in f.items() if k != "jpeg_sha256"} for f in man["frames"]]
    if again != frozen:
        raise SystemExit("manifest frames differ from a fresh selection; do not reselect")
    for f in man["frames"]:
        if hashlib.sha256((gold / "frames" / f["frame"]).read_bytes()).hexdigest() != f["jpeg_sha256"]:
            raise SystemExit(f"frame {f['frame']} changed since the manifest")


async def run_phase(phase: str, gold: Path, concurrency: int) -> dict:
    from meta.transport import HttpTransport

    path = OUT / MANIFEST
    if not committed(path):
        raise SystemExit(f"{path.name} must be committed before any paid call")
    man = json.loads(path.read_text())
    check_manifest(man, gold)
    transport = HttpTransport(timeout=180)
    try:
        runner = Runner(
            transport, OUT, lambda f: (gold / "frames" / f["frame"]).read_bytes(), PHASES[phase]["cap"]
        )
        print(f"phase {phase}: cap {runner.budget.cap} USD, spent before {runner.budget.spent:.6f} USD")
        if phase == "wonder":
            with wonder_allowed():
                return await runner.run(plan_for(phase, man["frames"]), concurrency)
        return await runner.run(plan_for(phase, man["frames"]), concurrency)
    finally:
        await transport.aclose()


def ledger_by_config(ledger: list[dict], rows: list[dict]) -> dict:
    """Spend per configuration from the model_call events, joined to the call rows by event id. Two
    configurations share a model, so the model alone cannot split them; an event no row names is 'unattributed'."""
    config = {i: r["config"] for r in rows for i in r.get("event_ids", [])}
    out: dict = {}
    for e in ledger:
        k = config.get(e["event_id"], "unattributed")
        d = out.setdefault(k, {"model_calls": 0, "usd": 0.0})
        d["model_calls"] += 1
        d["usd"] = round(d["usd"] + e["cost_usd"], 8)
    return dict(sorted(out.items()))


def do_score(out: Path = OUT) -> dict:
    man = json.loads((out / MANIFEST).read_text())
    rows = read_jsonl(out / ROWS)
    rows = [r for r in rows if "note" not in r]  # a refused-effort attempt is kept in the file, not scored
    ledger = read_jsonl(out / LEDGER)
    result = sc.score(rows, man["frames"], CATALOG, man["category_map"])
    result["ledger"] = {
        "model_calls": len(ledger),
        "ok": sum(e["ok"] for e in ledger),
        "usd": round(sum(e["cost_usd"] for e in ledger), 8),
        "by_model": {
            m: {
                "calls": sum(e["model"] == m for e in ledger),
                "usd": round(sum(e["cost_usd"] for e in ledger if e["model"] == m), 8),
            }
            for m in sorted({e["model"] for e in ledger})
        },
        "no_reply_posts": sum(no_reply(e) for e in ledger),
        "by_config": ledger_by_config(ledger, read_jsonl(out / ROWS)),
        "billing": "not reconciled with Billing",
    }
    result["comment_links"] = {
        arm: sc.comment_links(
            result["rows"], arm, man["frames"], man["no_card_comments"], CATALOG, man["price_changes"]
        )
        for arm in result["arms"]
    }
    return result


def table(result: dict) -> str:
    lines = [
        "arm | n | clear ok/wrong/abst/cant/err | boundary ok/wrong/no-pick | no_product wrong/abst | "
        "wrong all | gated wrong | p50 ms | p95 ms | USD | USD/350"
    ]
    for k, a in result["arms"].items():
        c, b, z = a["clear"], a["by_stratum"].get("boundary", {}), a["by_stratum"].get("no_product", {})
        lines.append(
            f"{k} | {a['n']} | {c['correct']}/{c['wrong']}/{c['abstain']}/{c['cannot_name']}/{c['error']} | "
            f"{b.get('correct', 0)}/{b.get('wrong', 0)}/"
            f"{b.get('abstain', 0) + b.get('cannot_name', 0) + b.get('error', 0)} | "
            f"{z.get('wrong', 0)}/{z.get('abstain', 0)} | {a['all']['wrong']} | {a['gated']['all']['wrong']} | "
            f"{a['latency_ms']['p50']} | {a['latency_ms']['p95']} | {a['usd']['total']:.4f} | "
            f"{a['usd']['per_350_frames']}"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m measure.model_compare")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest")
    m.add_argument("--gold", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--phase", choices=sorted(PHASES), required=True)
    r.add_argument("--gold", type=Path, required=True)
    r.add_argument("--concurrency", type=int, default=3)
    sub.add_parser("score")
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    if a.cmd == "manifest":
        man = build_manifest(a.gold)
        (OUT / MANIFEST).write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n")
        strata = {}
        for f in man["frames"]:
            strata[f["stratum"]] = strata.get(f["stratum"], 0) + 1
        print(f"{len(man['frames'])} frames {strata} -> {OUT / MANIFEST}")
    elif a.cmd == "run":
        print(json.dumps(asyncio.run(run_phase(a.phase, a.gold, a.concurrency))))
    else:
        result = do_score()
        (OUT / SCORE).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
        print(table(result))
        print(json.dumps({"best": result["best"], "ledger": result["ledger"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
