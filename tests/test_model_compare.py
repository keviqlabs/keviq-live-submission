"""measure.model_compare: what each schema's answer picks, the scores, the frozen frame list, and the cap.
No network: the Model API is a FakeTransport."""

import json

import pytest

import meta.client as mc
from app.contracts import validate_event
from measure import model_compare as m
from measure import model_compare_score as sc
from meta import pricing
from meta.client import MetaClient, ModelError
from meta.meter import BudgetExceeded
from meta.transport import FakeTransport, Reply, responses_reply
from worker.vision import spark

CAT = m.CATALOG


def a(category, color, has=True, conf=0.9):
    return {"has_product": has, "category": category, "color": color, "size": None, "material": None,
            "confidence": conf}  # fmt: skip


def b(state="one_product", kind="giày", color="đen", sku="SP01", conf=0.9):
    return {"state": state, "kind": kind, "color": color, "sku": sku, "confidence": conf}


# --- what an answer picks --------------------------------------------------------------------------------------


def test_production_schema_cannot_name_items_that_share_other_and_a_colour():
    assert sc.decide("A", a("other", "đen"), CAT) | {"confidence": None} == {
        "sku": None, "outcome": "cannot_name", "why": "collision", "confidence": None}  # fmt: skip
    assert sc.decide("A", a("other", "trắng"), CAT)["why"] == "collision"  # SP08 and SP10
    assert sc.decide("A", a("sneaker", " Đen "), CAT)["sku"] == "SP01"  # colour cleaned as production does
    assert sc.decide("A", a("sneaker", None), CAT)["why"] == "collision"  # SP01, SP02, SP03
    assert sc.decide("A", a("sneaker", "hồng"), CAT)["why"] == "no_match"
    assert sc.decide("A", a("none", None, has=False), CAT)["outcome"] == "abstain"


def test_kind_and_colour_name_one_item_and_unclear_abstains():
    assert sc.decide("B0", b(kind="tay cầm", color="đen"), CAT)["sku"] == "SP07"
    assert sc.decide("B0", b(kind="bàn phím", color="trắng"), CAT)["sku"] == "SP10"
    assert sc.decide("B0", b(kind="giày", color=None), CAT)["why"] == "collision"
    assert sc.decide("B0", b(state="unclear"), CAT) == {
        "sku": None, "outcome": "abstain", "why": "unclear", "confidence": 0.9}  # fmt: skip
    assert sc.decide("B0", b(kind="khác"), CAT)["outcome"] == "abstain"


def test_item_code_is_a_pick_and_unsure_is_an_abstain():
    assert sc.decide("B", b(state="unclear", sku="SP09"), CAT)["sku"] == "SP09"  # the code decides
    assert sc.decide("B", b(sku="unsure"), CAT)["outcome"] == "abstain"
    assert sc.decide("B", b(sku="none"), CAT)["outcome"] == "abstain"
    assert sc.decide("B", None, CAT)["outcome"] == "error"


def test_production_gate_is_a_separate_view():
    d = sc.decide("B", b(sku="SP01", conf=0.4), CAT)
    assert d["outcome"] == "pick" and sc.gated(d)["outcome"] == "abstain"
    assert sc.gated(sc.decide("B", b(sku="SP01", conf=0.5), CAT))["outcome"] == "pick"


def test_verdicts_by_stratum():
    pick = {"sku": "SP02", "outcome": "pick"}
    assert sc.verdict({"stratum": "boundary", "allowed": ["SP01", "SP02"], "gold": "SP01"}, pick) == "correct"
    assert sc.verdict({"stratum": "boundary", "allowed": ["SP03"], "gold": None}, pick) == "wrong"
    assert sc.verdict({"stratum": "no_product", "allowed": [], "gold": None}, pick) == "wrong"
    assert sc.verdict({"stratum": "same_colour_fashion", "allowed": [], "gold": "SP01"}, pick) == "wrong"
    assert (
        sc.verdict({"stratum": "no_product", "allowed": [], "gold": None}, {"outcome": "abstain"})
        == "abstain"
    )


# --- frozen frame list -----------------------------------------------------------------------------------------


def labels():
    segs = [
        {"product": None, "cue_s": 0.0, "change_band_s": [-1.0, 2.0]},
        {"product": "SP01", "cue_s": 5.2, "change_band_s": [4.0, 6.0]},
        {"product": "SP03", "cue_s": 15.4, "change_band_s": [14.0, 16.0]},
    ]
    seconds = []
    for t in range(25):
        product = None if t < 5 else "SP01" if t < 15 else "SP03"
        kind = {None: None, "SP01": "giày", "SP03": "giày"}[product]
        color = {None: None, "SP01": "đen", "SP03": "xám"}[product]
        seconds.append(
            {
                "t": t,
                "product": product,
                "kind": kind,
                "color": color,
                "boundary": 4 <= t <= 6 or 14 <= t <= 16,
            }
        )
    return {"segments": segs, "seconds": seconds}


def comment(cid, ts_ms, sku, ref="current_card"):
    return {"comment_id": cid, "ts_ms": ts_ms, "order": {"product": sku, "reference_kind": ref}}


def test_selection_strata_comment_frames_and_order_are_fixed():
    cs = [comment("c-1", 20_500, "SP03"), comment("c-2", 21_000, "SP01", "previous_card")]
    frames = sc.select(labels(), CAT, cs, per_sku=2, no_product=2, last_frame=24)
    assert frames == sc.select(labels(), CAT, cs, per_sku=2, no_product=2, last_frame=24)
    by_t = {f["t"]: f for f in frames}
    assert {t for t, f in by_t.items() if f["stratum"] == "boundary"} == {5, 15}
    assert by_t[15]["allowed"] == ["SP01", "SP03"] and by_t[5]["allowed"] == ["SP01"]
    assert {t for t, f in by_t.items() if f["stratum"] == "no_product"} == {1, 3}  # spread over seconds 0-3
    assert by_t[20]["comments"] == ["c-1"] and by_t[20]["stratum"] == "single_unique_colour"
    assert (
        by_t[13]["comments"] == ["c-2"] and by_t[13]["gold"] == "SP01"
    )  # last clear SP01 second before 21 s
    assert sorted(f["order"] for f in frames) == list(range(len(frames)))
    first = sorted(frames, key=lambda f: f["order"])[:4]
    assert len({f["gold"] if f["stratum"] in sc.CLEAR else f["stratum"] for f in first}) == 4


def test_prompts_carry_no_gold_and_only_b_lists_the_items():
    jpeg = b"\xff\xd8x"
    assert m.frame_input("A", jpeg) == spark.frame_input(jpeg, None)
    assert "chưa có" in m.frame_input("A", jpeg)[0]["content"][0]["text"]
    assert not any(s in m.PROMPT_B0 for s in sc.SKUS)
    assert all(f"{s}:" in m.PROMPT_B for s in sc.SKUS)
    assert m.SCHEMAS["B"]["properties"]["sku"]["enum"] == [*sc.SKUS, "unsure", "none"]


def test_blockers_name_reference_size_stock_and_price():
    pcs = [{"sku": "SP01", "from_s": 37.5, "to_s": 50.4, "new_price": 220000}]
    c = {
        "comment_id": "x",
        "ts_ms": 339_875,
        "order": {"product": "SP01", "size": "40", "qty": 3, "reference_kind": None},
    }
    out = sc.blockers(c, CAT, pcs)
    assert out[0].startswith("stock: qty 3 > stock 2") and out[1].startswith("price: spoken 220000")
    c = {
        "comment_id": "y",
        "ts_ms": 109_000,
        "order": {"product": "SP03", "size": None, "qty": 1, "reference_kind": "current_card"},
    }
    assert sc.blockers(c, CAT, pcs) == ["size: none given, item has ['41', '42']; ask the customer"]
    c = {
        "comment_id": "z",
        "ts_ms": 240_300,
        "order": {"product": "SP07", "size": None, "qty": 1, "reference_kind": "previous_card"},
    }
    assert sc.blockers(c, CAT, pcs) == ["reference: names an earlier item ('vừa nãy'), not the one on screen"]


# --- scores ----------------------------------------------------------------------------------------------------


def row(t, config, schema, data, cost=0.001, latency=100):
    return {"t": t, "config": config, "schema": schema, "ok": data is not None, "data": data, "cost_usd": cost,
            "latency_ms": latency, "tokens_in": 10, "tokens_out": 5}  # fmt: skip


def test_score_counts_behaviour_and_pairs_arms_on_shared_frames():
    frames = [
        {"t": 1, "gold": "SP01", "kind": "giày", "color": "đen", "stratum": "same_colour_fashion", "allowed": [],
         "comments": []},
        {"t": 2, "gold": None, "kind": None, "color": None, "stratum": "no_product", "allowed": [], "comments": []},
    ]  # fmt: skip
    rows = [
        row(1, "x", "B", b(sku="SP01")),
        row(2, "x", "B", b(state="no_product", kind="none", color=None, sku="SP02", conf=0.3)),
        row(1, "y", "B", b(sku="unsure")),
    ]
    out = sc.score(rows, frames, CAT, {"giày": ["sneaker"]})
    x = out["arms"]["x/B"]
    assert x["clear"]["correct"] == 1 and x["by_stratum"]["no_product"]["wrong"] == 1
    assert x["gated"]["by_stratum"]["no_product"]["abstain"] == 1  # the gate hides the low-confidence pick
    assert out["arms"]["y/B"]["clear"]["abstain"] == 1
    assert out["paired"]["B"]["frames"] == 1 and out["paired"]["B"]["arms"]["x/B"]["n"] == 1
    assert out["best"] == "x/B"  # y/B did not read every frame, so it cannot be best
    assert x["usd"]["per_350_frames"] == pytest.approx(0.35)
    frames[0]["comments"] = ["c-9"]
    c = {
        "comment_id": "c-9",
        "ts_ms": 1000,
        "order": {"product": "SP01", "size": "40", "qty": 1, "reference_kind": "current_card"},
    }
    link = sc.comment_links(out["rows"], "x/B", frames, [c], CAT, [])[0]
    assert link["valid_sku_card"] and link["valid_sku_card_gated"] and link["still_blocks"] == []
    assert link["read"]["sku"] == "SP01" and link["item_frames_correct"] == "1/1"


# --- cap, ledger, Wonder ---------------------------------------------------------------------------------------

FRAME = {"t": 7, "frame": "f_00007.jpg"}


def runner(tmp_path, replies, cap=5.0, configs=None):
    t = FakeTransport(replies)
    return m.Runner(t, tmp_path, lambda f: b"\xff\xd8x", cap, configs), t


async def test_a_call_the_cap_cannot_cover_is_not_sent(tmp_path):
    r, t = runner(tmp_path, [responses_reply(json.dumps(b()))], cap=0.001)
    assert await r.one(FRAME, "spark-minimal", "B") is None
    assert t.requests == [] and r.stopped.startswith("cap:")
    assert not (tmp_path / m.LEDGER).exists()


async def test_a_call_is_logged_as_a_model_call_and_its_hold_is_released(tmp_path):
    r, t = runner(tmp_path, [responses_reply(json.dumps(b(sku="SP03")), 1000, 50)])
    out = await r.one(FRAME, "spark-high", "B")
    events = m.read_jsonl(tmp_path / m.LEDGER)
    assert len(events) == 1 and validate_event(events[0])["purpose"] == "model_compare"
    assert (
        t.requests[0][1]["reasoning"] == {"effort": "high"} and t.requests[0][1]["model"] == "muse-spark-1.3"
    )
    cost = (1000 * 1.25 + 50 * 4.25) / 1e6
    assert out["cost_usd"] == pytest.approx(cost) and r.budget.spent == pytest.approx(cost)
    assert r.budget.held == 0 and out["data"]["sku"] == "SP03" and out["posts"] == 1
    assert m.read_jsonl(tmp_path / m.ROWS) == [out]


async def test_each_post_must_be_covered_before_it_is_sent(tmp_path):
    r, t = runner(
        tmp_path, [responses_reply("not json", 1000, 50), responses_reply(json.dumps(b()), 1000, 50)]
    )
    w = r.w_post("spark-minimal")
    r.budget.spent = r.budget.cap - 2 * w  # room for the call's two POSTs, no more
    out = await r.one(FRAME, "spark-minimal", "B")
    assert out["ok"] and out["posts"] == 2 and len(t.requests) == 2 and r.budget.held == 0
    meter = m.CapMeter(m.Budget(cap=1.0, spent=1.0))
    token = m.CURRENT.set(m.Call("spark-minimal", 0.01))
    try:
        with pytest.raises(BudgetExceeded):
            await meter.check()
    finally:
        m.CURRENT.reset(token)


class Down:
    requests: list = []

    async def post(self, path, body):
        raise ConnectionError("no route")


async def test_a_post_with_no_reply_keeps_its_worst_case_as_spent(tmp_path):
    r = m.Runner(Down(), tmp_path, lambda f: b"x", 5.0)
    w = r.w_post("spark-minimal")
    out = await r.one(FRAME, "spark-minimal", "A")
    assert out["error"] == "transport_ConnectionError" and out["unknown_cost_posts"] == 1
    assert r.budget.unknown == pytest.approx(w) and r.budget.held == 0
    again = m.Runner(Down(), tmp_path, lambda f: b"x", 5.0)  # a restart cannot tell the effort: larger one
    assert (
        again.budget.unknown == pytest.approx(again.w_post("spark-high")) and again.w_post("spark-high") > w
    )
    assert (7, "spark-minimal", "A") in again.done


async def test_wonder_is_allowed_and_priced_only_inside_the_experiment(tmp_path):
    reply = json.dumps(b(sku="SP09"))
    r, t = runner(tmp_path, [responses_reply(reply, 1000, 50)], configs={"wonder-high": (m.WONDER, "high")})
    with pytest.raises(ModelError):  # still BLOCKED outside
        await MetaClient(FakeTransport([]), m.CapMeter(r.budget), r.client.emit).responses(
            "x", {}, "p", m.WONDER
        )
    with m.wonder_allowed():
        out = await r.one(FRAME, "wonder-high", "B")
    assert out["cost_usd"] == pytest.approx((1000 * 4.00 + 50 * 20.00) / 1e6)
    assert mc.BLOCKED is pricing.BLOCKED and mc.cost_usd is pricing.cost_usd
    assert m.WONDER in pricing.BLOCKED and m.WONDER not in pricing.PRICES
    assert t.requests[0][1]["model"] == m.WONDER


async def test_a_resumed_run_skips_done_calls_and_counts_their_spend(tmp_path):
    r, _ = runner(tmp_path, [responses_reply(json.dumps(a("sneaker", "đen")), 1000, 50)])
    assert m.plan_for("spark", [FRAME])[0][1:] == ("spark-minimal", "A")
    await r.run(m.plan_for("spark", [FRAME])[:1], log=lambda s: None)
    again, t = runner(tmp_path, [])
    summary = await again.run(m.plan_for("spark", [FRAME])[:1], log=lambda s: None)
    assert summary["already_done"] == 1 and summary["called"] == 0 and t.requests == []
    assert again.budget.spent == pytest.approx(r.budget.spent) and again.seen["spark-minimal"]["in"] == 1000


def test_spend_is_split_by_configuration_from_the_ledger():
    ledger = [
        {"event_id": "a", "cost_usd": 0.5},
        {"event_id": "b", "cost_usd": 0.25},
        {"event_id": "c", "cost_usd": 1},
    ]
    rows = [{"config": "spark-minimal", "event_ids": ["a"]}, {"config": "spark-high", "event_ids": ["b"]}]
    assert m.ledger_by_config(ledger, rows) == {
        "spark-high": {"model_calls": 1, "usd": 0.25},
        "spark-minimal": {"model_calls": 1, "usd": 0.5},
        "unattributed": {"model_calls": 1, "usd": 1.0},
    }


def test_http_error_reply_is_not_a_no_reply():
    assert not m.no_reply({"error": "http_429"}) and m.no_reply({"error": "transport_ReadTimeout"})
    assert Reply(200, {}).status == 200
