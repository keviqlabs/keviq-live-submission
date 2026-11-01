from answers.selector import Selector, replay


def card(
    card_id="c1",
    version=2,
    sku="SP01",
    name="SP01 giày đen",
    price=250000,
    status="live",
    frame=1000,
    variants=None,
):
    if variants is None:
        variants = [{"size": "40", "color": "đen", "stock": 2}, {"size": "41", "color": "đen", "stock": 1}]
    return {
        "event_id": f"{card_id}.v{version}", "session_id": "test", "ts_ms": frame, "source": "replay", "v": 1,
        "type": "card", "card_id": card_id, "version": version, "sku": sku, "name": name, "price": price,
        "currency": "VND", "variants": variants, "stock": sum(v["stock"] for v in variants), "image_url": None,
        "status": status, "provenance": {"frame_ts_ms": frame, "speech_event_ids": [], "edited_by": None},
    }  # fmt: skip


SHOES2 = dict(card_id="c2", sku="SP02", name="SP02 giày trắng", price=320000, frame=40000,
              variants=[{"size": "39", "color": "trắng", "stock": 1}, {"size": "40", "color": "trắng", "stock": 2}])  # fmt: skip


def ev_card(t, **kw):
    return {"t_ms": t, "type": "card", "payload": card(**kw)}


def ev_q(t, cid, text, label=None):
    return {"t_ms": t, "type": "comment", "comment_id": cid, "text": text, "label": label}


def spoken(decisions):
    return [d for d in decisions if d.action != "silent"]


def test_groups_same_question_into_one_answer():
    out = replay([ev_card(0), ev_q(10000, "a", "Size 40 còn không shop"), ev_q(12000, "b", "Còn size 40 không ạ"),
                  ev_q(30000, "end", "Mic rè quá shop")])  # fmt: skip
    assert [(d.comment_ids, d.text) for d in spoken(out)] == [(("a", "b"), "Giày đen size 40 còn 2 ạ.")]
    assert spoken(out)[0].ts_ms == 10000 + Selector().window_ms


def test_spoken_decisions_keep_a_gap():
    s = Selector()
    out = replay([ev_card(0), ev_q(10000, "a", "Size 40 còn không shop"), ev_q(10500, "b", "Giá bao nhiêu vậy ạ"),
                  ev_q(11000, "c", "Size 41 còn không shop"), ev_q(40000, "end", "hi")])  # fmt: skip
    times = [d.ts_ms for d in spoken(out)]
    assert len(times) == 3 and all(b - a >= s.cooldown_ms for a, b in zip(times, times[1:], strict=False))


def test_item_switch_drops_pending_group():
    out = replay([ev_card(0), ev_q(10000, "a", "Size 40 còn không shop"), ev_card(12000, **SHOES2),
                  ev_q(40000, "end", "hi")])  # fmt: skip
    assert not spoken(out)
    assert [(d.comment_ids, d.reason) for d in out] == [(("a",), "item_changed")]


def test_question_right_after_switch_is_ambiguous():
    out = replay([ev_card(0), ev_card(40000, **SHOES2), ev_q(41000, "a", "Size 41 còn không shop"),
                  ev_q(60000, "end", "hi")])  # fmt: skip
    assert not spoken(out) and out[0].reason == "just_switched"


def test_price_edit_before_answer_uses_new_price():
    out = replay([ev_card(0), ev_q(10000, "a", "Giá bao nhiêu vậy ạ"),
                  ev_card(12000, version=3, price=230000, status="edited"), ev_q(30000, "end", "hi")])  # fmt: skip
    assert [d.text for d in spoken(out)] == ["Giày đen giá 230 nghìn đồng ạ."]
    assert spoken(out)[0].version == 3


def test_late_older_version_is_ignored():
    out = replay([ev_card(0), ev_card(5000, version=3, price=230000, status="edited"),
                  ev_card(5200, version=2), ev_q(10000, "a", "Giá bao nhiêu vậy ạ"), ev_q(30000, "end", "hi")])  # fmt: skip
    assert [d.text for d in spoken(out)] == ["Giày đen giá 230 nghìn đồng ạ."]


def test_same_card_shown_again_counts_as_a_switch():
    s = Selector()
    s.card(card(), 0)
    s.card(card(**SHOES2), 40000)
    s.card(card(version=3, frame=80000), 80000)
    assert s.board.current().card_id == "c1"
    assert s.comment("a", 81000, "Size 40 còn không shop")[0].reason == "just_switched"


def test_non_questions_and_sized_orders_make_no_decision():
    out = replay([ev_card(0), ev_q(10000, "a", "Mic rè quá shop", "complaint"),
                  ev_q(11000, "b", "Lấy đôi này size 40 cho chị", "order"), ev_q(30000, "end", "hi")])  # fmt: skip
    assert out == []


def test_ask_size_for_an_order_without_size():
    out = replay([ev_card(0), ev_q(10000, "a", "Lấy đôi này cho em nhé", "order"), ev_q(30000, "end", "hi")])
    assert [(d.action, d.text) for d in out] == [
        ("ask", "Bạn lấy size nào ạ? Giày đen còn size 40, size 41.")
    ]
    assert out[0].ts_ms - 10000 <= 5000


def test_backlog_is_dropped_not_spoken_late():
    s = Selector()
    events = [ev_card(0)] + [
        ev_q(10000 + i * 100, f"q{i}", f"Size {40 + i % 2} còn không shop") for i in range(2)
    ]
    events += [ev_q(10300, "p", "Giá bao nhiêu vậy ạ"), ev_q(10400, "s", "Còn mấy cái vậy shop")]
    events += [ev_q(10500, "x", "Giá có bao ship chưa ạ"), ev_q(90000, "end", "hi")]
    out = replay(events)
    assert all(d.ts_ms - 10000 <= s.max_wait_ms for d in spoken(out))
    assert any(d.reason == "too_late" for d in out)
