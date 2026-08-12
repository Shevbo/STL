"""Smart-orders engine: pure trigger/trailing/OCO/on-fill/grid logic."""

import pytest

from trader.quik.smart_orders import (
    Cancel,
    Fire,
    SmartOrder,
    SmartOrderBook,
    evaluate,
    marketable_price,
    new_id,
    quantize,
    protective_children,
    superseded_stops,
)

NOW = 1_784_700_000_000
STEP = 10.0


def so(**kw):
    base = dict(so_id=new_id(), kind="sl", code="RIU6", side="sell", qty=1,
                created_ms=NOW)
    base.update(kw)
    return SmartOrder(**base)


def run(orders, *, last, bid=0.0, ask=0.0, filled=None, tick_ms=NOW, now=NOW,
        step=STEP, session_open=True):
    return evaluate(orders, "RIU6", last=last, bid=bid or last - STEP,
                    ask=ask or last + STEP, tick_ms=tick_ms, now_ms=now,
                    filled_client_ids=filled or set(), step=step,
                    session_open=session_open)


# ---- биржа не торгует: ни следить, ни стрелять (инцидент 06.08.2026) ----

def test_closed_market_neither_activates_nor_fires():
    """Кадр приходит свежим и вне сессии (QUIK переиздаёт нерыночные значения,
    агент штампует время ПРИХОДА). Пока оракул не сказал «торгуем», заявка не
    имеет права ни активироваться, ни сработать."""
    o = so(kind="trail_tp", side="buy", trigger_price=89260, trail_offset=430)
    assert run([o], last=89120, session_open=False) == []
    assert not o.activated and o.peak == 0        # фантомный пик не записан
    assert run([o], last=90130, session_open=False) == []
    assert o.status == "armed"
    assert run([o], last=89120, session_open=None) == []   # оракул молчит — не торгуем
    assert not o.activated


def test_mid_of_preopen_book_never_fires():
    """last=0 (сделок в сессии ещё нет) — середина неспаренного стакана ценой
    не является: раньше по ней активировался и стрелял трейл."""
    o = so(kind="trail_tp", side="buy", trigger_price=89260, trail_offset=430)
    assert run([o], last=0.0, bid=78140, ask=90100) == []
    assert not o.activated


# ---- SL / TP triggers ----

def test_sl_long_fires_below_trigger_only():
    o = so(kind="sl", side="sell", trigger_price=83000)
    assert run([o], last=83010) == []
    acts = run([o], last=83000)
    assert len(acts) == 1 and isinstance(acts[0], Fire)
    assert acts[0].price < 83000              # crosses down, marketable
    assert acts[0].price % STEP == 0          # on the exchange grid


def test_sl_short_fires_above_trigger():
    o = so(kind="sl", side="buy", trigger_price=84000)
    assert run([o], last=83990) == []
    acts = run([o], last=84000)
    assert len(acts) == 1 and acts[0].price > 84000 and acts[0].price % STEP == 0


def test_tp_long_fires_at_or_above_target():
    o = so(kind="tp", side="sell", trigger_price=84200)
    assert run([o], last=84190) == []
    acts = run([o], last=84200)
    assert len(acts) == 1 and isinstance(acts[0], Fire)


def test_tp_short_fires_at_or_below_target():
    o = so(kind="tp", side="buy", trigger_price=82000)
    assert run([o], last=82010) == []
    assert len(run([o], last=82000)) == 1


# ---- trailing ----

def test_trail_tp_long_tracks_peak_then_fires_on_retrace():
    o = so(kind="trail_tp", side="sell", trigger_price=0, trail_offset=100)
    assert run([o], last=83000) == []          # activates, peak=83000
    assert run([o], last=83500) == []          # peak=83500
    assert run([o], last=83450) == []          # retrace 50 < 100
    acts = run([o], last=83400)                # retrace 100 -> fire
    assert len(acts) == 1 and isinstance(acts[0], Fire)


def test_trail_tp_activation_level_gates_tracking():
    o = so(kind="trail_tp", side="sell", trigger_price=84000, trail_offset=50)
    # Below activation: a swing down must NOT fire (not armed yet).
    assert run([o], last=83500) == []
    assert run([o], last=83300) == []
    assert not o.activated
    assert run([o], last=84000) == []          # activation
    assert o.activated
    assert run([o], last=84200) == []          # peak 84200
    assert len(run([o], last=84150)) == 1      # retrace 50


def test_trail_tp_short_mirror():
    o = so(kind="trail_tp", side="buy", trigger_price=0, trail_offset=100)
    run([o], last=83000)                        # trough 83000
    assert run([o], last=82500) == []           # trough 82500
    assert len(run([o], last=82600)) == 1       # retrace 100 up -> cover


# ---- on_fill (conditional / order-sends-order) ----

def test_on_fill_waits_for_watched_fill():
    o = so(kind="on_fill", side="sell", watch_client_id="op-entry-1",
           child_price=84300)
    assert run([o], last=84000) == []
    acts = run([o], last=84000, filled={"op-entry-1"})
    assert len(acts) == 1 and acts[0].price == 84300


def test_on_fill_marketable_child_when_no_price_given():
    o = so(kind="on_fill", side="sell", watch_client_id="e1", child_price=0)
    acts = run([o], last=84000, bid=84000, ask=84020, filled={"e1"})
    assert len(acts) == 1
    assert acts[0].price < 84000 and acts[0].price % STEP == 0


# ---- OCO bracket ----

def test_oco_fire_cancels_sibling():
    sl = so(kind="sl", side="sell", trigger_price=83000, oco_group="br1")
    tp = so(kind="tp", side="sell", trigger_price=84500, oco_group="br1")
    acts = run([sl, tp], last=82990)
    kinds = {type(a) for a in acts}
    assert kinds == {Fire, Cancel}
    fired = next(a for a in acts if isinstance(a, Fire))
    cancelled = next(a for a in acts if isinstance(a, Cancel))
    assert fired.so is sl and cancelled.so is tp


# ---- safety rails ----

def test_stale_tick_never_fires():
    o = so(kind="sl", side="sell", trigger_price=83000)
    assert run([o], last=82000, tick_ms=NOW - 60_000, now=NOW) == []


def test_expiry_marks_expired_without_firing():
    o = so(kind="sl", side="sell", trigger_price=83000, good_till_ms=NOW - 1)
    assert run([o], last=82000) == []
    assert o.status == "expired"


def test_grid_quantize_directions():
    assert quantize(83533.12, 10, "sell") == 83530
    assert quantize(83533.12, 10, "buy") == 83540
    assert quantize(86.634, 0.01, "sell") == pytest.approx(86.63)


def test_marketable_cushion_under_collar():
    px = marketable_price("sell", bid=84000, ask=84020, last=84010, step=10)
    assert px % 10 == 0
    assert 0 < (84000 - px) <= 84000 * 0.0015   # crosses, but under the collar


# ---- persistence ----

def test_book_round_trip(tmp_path):
    p = str(tmp_path / "so.json")
    b = SmartOrderBook(p)
    b.add(so(kind="trail_tp", trail_offset=100, peak=83500, activated=True))
    b2 = SmartOrderBook(p)
    b2.load()
    assert len(b2.orders) == 1
    got = b2.orders[0]
    assert got.kind == "trail_tp" and got.peak == 83500 and got.activated


def test_book_corrupt_file_survives(tmp_path):
    p = str(tmp_path / "so.json")
    with open(p, "w") as f:
        f.write("{broken")
    b = SmartOrderBook(p)
    b.load()
    assert b.orders == []


def test_validate_rejects_nonsense():
    assert so(kind="wat").validate()
    assert so(kind="sl", trigger_price=0).validate()
    assert so(kind="trail_tp", trail_offset=0).validate()
    assert so(kind="on_fill", watch_client_id="").validate()
    assert so(kind="sl", trigger_price=83000).validate() is None


# ---- trail catch-up (активация, пропущенная за простой STL) ----

def test_catch_up_activates_buy_on_window_low():
    from trader.quik.smart_orders import catch_up_trail
    o = so(kind="trail_tp", side="buy", trigger_price=88500, trail_offset=350)
    assert catch_up_trail(o, wmin=88270, wmax=89600)
    assert o.activated and o.peak == 88270


def test_catch_up_ignores_uncrossed_and_activated():
    from trader.quik.smart_orders import catch_up_trail
    o = so(kind="trail_tp", side="buy", trigger_price=88500, trail_offset=350)
    assert not catch_up_trail(o, wmin=88510, wmax=89600)   # минимум не дошёл
    assert not o.activated
    o2 = so(kind="trail_tp", side="sell", trigger_price=90000, trail_offset=350,
            activated=True, peak=90100)
    assert not catch_up_trail(o2, wmin=88000, wmax=91000)  # уже активна — не трогаем
    assert o2.peak == 90100


def test_catch_up_sell_on_window_high():
    from trader.quik.smart_orders import catch_up_trail
    o = so(kind="trail_tp", side="sell", trigger_price=90000, trail_offset=350)
    assert catch_up_trail(o, wmin=88000, wmax=90050)
    assert o.activated and o.peak == 90050


# ---- защитный стоп после входа (trail/on_fill) ----

def test_protective_sl_after_buy_entry():
    from trader.quik.smart_orders import protective_sl
    parent = so(kind="trail_tp", side="buy", trigger_price=88500, trail_offset=350,
                qty=14, sl_offset=300)
    child = protective_sl(parent, entry_price=89000, now=NOW)
    assert child is not None
    assert child.kind == "sl" and child.side == "sell"      # вход покупкой -> выход продажей
    assert child.trigger_price == 88700                     # 89000 - 300
    assert child.qty == 14 and child.code == parent.code
    assert child.parent_id == parent.so_id
    assert child.validate() is None


def test_protective_sl_after_sell_entry_is_mirrored():
    from trader.quik.smart_orders import protective_sl
    parent = so(kind="trail_tp", side="sell", trail_offset=200, qty=2, sl_offset=150)
    child = protective_sl(parent, entry_price=90000, now=NOW)
    assert child.side == "buy" and child.trigger_price == 90150


def test_protective_sl_off_by_default_and_on_bad_price():
    from trader.quik.smart_orders import protective_sl
    plain = so(kind="trail_tp", side="buy", trail_offset=350)
    assert protective_sl(plain, entry_price=89000, now=NOW) is None      # sl_offset=0
    armed = so(kind="trail_tp", side="buy", trail_offset=350, sl_offset=300)
    assert protective_sl(armed, entry_price=0, now=NOW) is None          # нет цены входа


def test_protective_sl_fires_like_a_normal_stop():
    """Родившийся стоп обязан вести себя как обычный SL: продажа при цене <= порога."""
    from trader.quik.smart_orders import protective_sl
    parent = so(kind="trail_tp", side="buy", trail_offset=350, qty=1, sl_offset=300)
    child = protective_sl(parent, entry_price=89000, now=NOW)
    assert run([child], last=88710) == []                    # ещё выше порога
    acts = run([child], last=88700)
    assert len(acts) == 1 and isinstance(acts[0], Fire)


def test_after_fill_pair_allowed_on_every_kind():
    """Раньше защитная пара разрешалась только следящей и зависимой. Любая умная
    заявка только ВХОДИТ и после срабатывания забывает про позицию, поэтому
    блоки «после сделки» нужны везде (09.08.2026)."""
    assert so(kind="sl", side="sell", trigger_price=88000,
              sl_offset=100, tp_offset=200).validate() is None
    assert so(kind="tp", side="buy", trigger_price=88000,
              sl_offset=100, tp_offset=200).validate() is None
    # отрицательные по-прежнему запрещены
    assert so(kind="sl", side="sell", trigger_price=88000, sl_offset=-1).validate()


def test_conditional_kind_spawns_the_after_fill_pair():
    """Условная (kind=sl) отработала -> из её цены рождаются стоп и тейк, как у
    следящей: механика _protective от типа родителя не зависит."""
    from trader.quik.smart_orders import protective_children
    parent = so(kind="sl", side="buy", trigger_price=88000, qty=3,
                sl_offset=300, tp_offset=500)
    kids = protective_children(parent, entry_price=89000, now=NOW)
    assert [(k.kind, k.side, k.trigger_price) for k in kids] == [
        ("sl", "sell", 88700), ("tp", "sell", 89500)]
    assert kids[0].oco_group == kids[1].oco_group != ""       # одна связка
    # ВНУКОВ НЕТ: сами защитные заявки offset'ов не несут, рекурсия невозможна
    assert all(protective_children(k, entry_price=89000, now=NOW) == [] for k in kids)


# ---- цена сделки у сработавшей заявки ----

def test_fill_price_is_vwap_of_quik_trades():
    """Лимит дочерней заявки — не цена сделки: она маркетабельная и часто
    исполняется лучше лимита, а крупная — несколькими сделками по разным ценам."""
    from trader.api.quik_smart_orders import _fill_price
    status = {"quik": {"trades": [
        {"order_num": "777", "qty": 10, "price": 88_340.0},
        {"order_num": "777", "qty": 4, "price": 88_350.0},
        {"order_num": "999", "qty": 5, "price": 90_000.0},   # чужая заявка
    ]}}
    px, vol = _fill_price(status, "777")
    assert vol == 14
    assert round(px, 4) == round((88_340 * 10 + 88_350 * 4) / 14, 4)


def test_fill_price_absent_when_no_trades_yet():
    from trader.api.quik_smart_orders import _fill_price
    assert _fill_price({"quik": {"trades": []}}, "777") == (0.0, 0)
    assert _fill_price({}, "777") == (0.0, 0)


def test_match_trades_fallback_after_restart():
    """Стор заявок в памяти обнуляется рестартом. Тогда сделку ищем по таблице
    QUIK: ручной класс, тот же инструмент и сторона, рядом по времени."""
    from trader.api.quik_smart_orders import _match_trades
    o = so(kind="trail_tp", side="buy", qty=14, trail_offset=350)
    o.fired_ms = NOW
    status = {"quik": {"trades": [
        {"order_num": "A", "sec": "RIU6", "side": "buy", "qty": 10, "price": 88_340.0,
         "ts_ms": NOW + 1_000, "tag": ""},
        {"order_num": "A", "sec": "RIU6", "side": "buy", "qty": 4, "price": 88_360.0,
         "ts_ms": NOW + 1_500, "tag": ""},
        {"order_num": "R", "sec": "RIU6", "side": "buy", "qty": 1, "price": 88_300.0,
         "ts_ms": NOW + 900, "tag": "l90z0afzceesll5izjjg"},      # робот — не наша
        {"order_num": "B", "sec": "BRU6", "side": "buy", "qty": 14, "price": 85.0,
         "ts_ms": NOW + 800, "tag": ""},                          # другой инструмент
    ]}}
    px, vol = _match_trades(status, o)
    assert vol == 14
    assert round(px, 2) == round((88_340 * 10 + 88_360 * 4) / 14, 2)


def test_match_trades_ignores_far_and_oversized():
    from trader.api.quik_smart_orders import _match_trades
    o = so(kind="trail_tp", side="sell", qty=2, trail_offset=100)
    o.fired_ms = NOW
    far = {"quik": {"trades": [{"order_num": "A", "sec": "RIU6", "side": "sell", "qty": 2,
                                "price": 90_000.0, "ts_ms": NOW + 20 * 60_000, "tag": ""}]}}
    assert _match_trades(far, o) == (0.0, 0)          # слишком далеко по времени
    # Окно 15 минут: заявка ждала встречный объём на открытии (06.08.2026).
    late = {"quik": {"trades": [{"order_num": "A", "sec": "RIU6", "side": "sell", "qty": 2,
                                 "price": 90_000.0, "ts_ms": NOW + 5 * 60_000, "tag": ""}]}}
    assert _match_trades(late, o) == (90_000.0, 2)
    big = {"quik": {"trades": [{"order_num": "A", "sec": "RIU6", "side": "sell", "qty": 9,
                                "price": 90_000.0, "ts_ms": NOW + 500, "tag": ""}]}}
    assert _match_trades(big, o) == (0.0, 0)          # объём больше нашего — чужая


# ---- защитная ПАРА (стоп + тейк) после входа ----

def test_protective_tp_is_in_favour_of_entry():
    from trader.quik.smart_orders import protective_tp
    buy = so(kind="trail_tp", side="buy", trail_offset=350, qty=14, tp_offset=500)
    tp = protective_tp(buy, entry_price=89_000, now=NOW)
    assert tp.kind == "tp" and tp.side == "sell"
    assert tp.trigger_price == 89_500          # прибыль лонга — ВЫШЕ входа
    sell = so(kind="trail_tp", side="sell", trail_offset=350, qty=14, tp_offset=500)
    tp2 = protective_tp(sell, entry_price=89_000, now=NOW)
    assert tp2.side == "buy" and tp2.trigger_price == 88_500   # прибыль шорта — НИЖЕ


def test_protective_pair_shares_one_oco_group():
    """Стоп и тейк стерегут ОДНУ позицию: сработал один — второй обязан сняться,
    иначе он откроет позицию в обратную сторону."""
    from trader.quik.smart_orders import protective_children
    p = so(kind="trail_tp", side="buy", trail_offset=350, qty=14,
           sl_offset=300, tp_offset=500)
    kids = protective_children(p, entry_price=89_000, now=NOW)
    assert [k.kind for k in kids] == ["sl", "tp"]
    assert kids[0].oco_group and kids[0].oco_group == kids[1].oco_group
    assert kids[0].trigger_price == 88_700 and kids[1].trigger_price == 89_500
    # и связка реально гасит вторую заявку
    acts = run(kids, last=88_700)
    fired = [a for a in acts if isinstance(a, Fire)]
    cancelled = [a for a in acts if isinstance(a, Cancel)]
    assert len(fired) == 1 and fired[0].so.kind == "sl"
    assert len(cancelled) == 1 and cancelled[0].so.kind == "tp"


def test_protective_pair_only_what_was_asked():
    from trader.quik.smart_orders import protective_children
    only_tp = so(kind="trail_tp", side="buy", trail_offset=350, tp_offset=500)
    assert [k.kind for k in protective_children(only_tp, 89_000, NOW)] == ["tp"]
    neither = so(kind="trail_tp", side="buy", trail_offset=350)
    assert protective_children(neither, 89_000, NOW) == []


def test_rebase_protective_moves_levels_to_real_entry():
    """Уровни ставятся по цене дочерней (маркетабельной) заявки, а исполняется она
    часто лучше лимита — как только известна фактическая цена, двигаем уровни."""
    from trader.quik.smart_orders import protective_children, rebase_protective
    p = so(kind="trail_tp", side="buy", trail_offset=350, qty=14,
           sl_offset=300, tp_offset=500)
    kids = protective_children(p, entry_price=89_000, now=NOW)
    assert rebase_protective(kids, p, real_entry=88_940) is True
    assert kids[0].trigger_price == 88_640 and kids[1].trigger_price == 89_440
    # уже сработавшую заявку не трогаем
    kids[0].status = "fired"
    before = kids[0].trigger_price
    rebase_protective(kids, p, real_entry=88_000)
    assert kids[0].trigger_price == before

# ---- подтягивающая (trail_sl): храповик на уже открытой позиции ----

def _tsl(side="sell", offset=100.0, **kw):
    kw.setdefault("so_id", "ts1")
    return so(kind="trail_sl", side=side, trail_offset=offset, **kw)


def _tick(order, price):
    """Один тик по живому рынку; True = заявка сработала."""
    return bool(run([order], last=price))


def test_trail_sl_ratchets_only_toward_less_loss():
    """Уровень идёт ЗА ценой в прибыль и НИКОГДА не откатывается: иначе это не
    подтягивающая, а обычный стоп, который оператор двигает руками."""
    o = _tsl()                              # лонг стережём продажей
    _tick(o, 100_000.0)                     # активация с первого тика
    assert o.activated and o.peak == 100_000.0
    _tick(o, 100_500.0)
    assert o.peak == 100_500.0              # цена в плюс — уровень подтянулся
    _tick(o, 100_300.0)
    assert o.peak == 100_500.0              # откат НЕ опускает уровень назад
    assert not _tick(o, 100_450.0)          # 50 пунктов отката из 100 — рано
    assert _tick(o, 100_400.0)              # ровно 100 от пика — закрытие


def test_trail_sl_rejects_activation_level_and_after_trade_blocks():
    """Оба запрета смысловые: уровень активации оставил бы позицию без стопа до
    пробоя, а блоки после сделки открыли бы НОВУЮ позицию после выхода."""
    assert "уровня активации" in (_tsl(trigger_price=99_000.0).validate() or "")
    assert "блоки после сделки" in (_tsl(sl_offset=50.0).validate() or "")
    assert "блоки после сделки" in (_tsl(tp_offset=50.0).validate() or "")
    assert _tsl(offset=0.0).validate()      # шаг обязателен
    assert _tsl().validate() is None


def test_trail_sl_on_short_mirrors():
    o = _tsl(side="buy")                    # шорт стережём покупкой
    _tick(o, 100_000.0)
    _tick(o, 99_400.0)
    assert o.peak == 99_400.0               # вниз = прибыль по шорту
    _tick(o, 99_450.0)
    assert o.peak == 99_400.0
    assert _tick(o, 99_500.0)               # +100 от минимума — закрытие


def test_new_trailing_supersedes_other_stops_on_the_same_position():
    """Два стопа на одной позиции: сработает ближний, дальний останется взведён
    и следующим ходом ОТКРОЕТ позицию в обратную сторону. Поэтому новая
    подтягивающая снимает прежние стопы того же направления."""
    old_sl = so(so_id="old", kind="sl", side="sell", trigger_price=99_000.0)
    old_tp = so(so_id="tp", kind="tp", side="sell", trigger_price=101_000.0)
    other_side = so(so_id="oth", kind="sl", side="buy", trigger_price=99_000.0)
    fresh = _tsl(so_id="new")
    ids = {o.so_id for o in superseded_stops([old_sl, old_tp, other_side, fresh], fresh)}
    assert ids == {"old"}          # тейк живёт, чужая сторона живёт, стоп снят


def test_trailing_after_trade_is_offered_to_any_kind():
    """«После сделки» теперь три блока, и подтягивающая доступна любому типу."""
    parent = so(kind="on_fill", side="buy", watch_client_id="c1", trail_after=300.0)
    kids = protective_children(parent, 89_000.0, NOW)
    assert [k.kind for k in kids] == ["trail_sl"]
    assert kids[0].side == "sell" and kids[0].trail_offset == 300.0
    assert kids[0].trigger_price == 0.0        # уровня у неё нет по определению
    # Обычный стоп и подтягивающая вместе запрещены: сработает ближний, дальний
    # останется и откроет позицию обратно.
    assert "сразу обычный стоп и подтягивающую" in (
        so(kind="on_fill", watch_client_id="c1", sl_offset=100.0,
           trail_after=300.0).validate() or "")
