"""Мини-график робота в панели компаньона: склейка филлов в маркеры сделок.

Зеркало хранит строку на КАЖДУЮ сделку QUIK. Тонкая вечерняя книга наливает один
ордер по частям, и без склейки ордер на 5 контрактов рисуется входом и четырьмя
фантомными «усреднениями» — та же ошибка, что однажды развела маркеры графика и
таблицу сделок на стенде робота.
"""

from trader.api.quik_companion import _merge_fills


def _fill(order_id, ts, price, qty, side="buy", status="filled"):
    return {"order_id": order_id, "ts_unix_ms": ts, "price": price,
            "qty": qty, "side": side, "status": status}


def test_one_order_filled_in_parts_is_one_marker():
    fills = [
        _fill("A", 1_000, 100.0, 1),
        _fill("A", 1_500, 102.0, 1),
        _fill("A", 2_000, 104.0, 2),
    ]
    got = _merge_fills(fills, lo_ms=0)
    assert len(got) == 1, "один ордер — один маркер"
    m = got[0]
    assert m["qty"] == 4
    # средневзвешенная: (100*1 + 102*1 + 104*2) / 4
    assert m["price"] == (100.0 + 102.0 + 104.0 * 2) / 4
    assert m["ts"] == 2_000, "время последнего филла: тогда ордер долился"


def test_separate_orders_stay_separate_and_sorted():
    got = _merge_fills([_fill("B", 3_000, 90.0, 1), _fill("A", 1_000, 80.0, 1)], lo_ms=0)
    assert [m["ts"] for m in got] == [1_000, 3_000]


def test_window_cuts_the_older_tail():
    fills = [_fill("A", 500, 100.0, 1), _fill("B", 5_000, 101.0, 1)]
    got = _merge_fills(fills, lo_ms=1_000)
    assert [m["ts"] for m in got] == [5_000]


def test_partial_of_the_same_order_before_the_window_does_not_skew_the_price():
    """Хвост длиннее окна: филл вне окна в среднюю попасть не должен."""
    fills = [_fill("A", 500, 50.0, 10), _fill("A", 5_000, 100.0, 1)]
    got = _merge_fills(fills, lo_ms=1_000)
    assert len(got) == 1
    assert got[0]["qty"] == 1 and got[0]["price"] == 100.0


def test_non_filled_and_junk_rows_are_dropped():
    fills = [
        _fill("A", 1_000, 100.0, 1, status="rejected"),
        _fill("B", 1_000, 100.0, 1, status="paper"),
        _fill("C", 1_000, 0.0, 1),          # цены нет
        _fill("D", 1_000, 100.0, 0),        # количества нет
        _fill("E", 1_000, None, 1),         # мусор
    ]
    assert _merge_fills(fills, lo_ms=0) == []


def test_fill_without_order_id_is_not_merged_with_a_stranger():
    """Пустой order_id встречается у синтезированных авто-хилом сделок."""
    fills = [_fill("", 1_000, 100.0, 1), _fill("", 2_000, 200.0, 1)]
    got = _merge_fills(fills, lo_ms=0)
    assert len(got) == 2, "разные цена/время — разные сделки, а не один ордер"


def test_empty_input_is_empty_output():
    assert _merge_fills([], lo_ms=0) == []


# ── Мини-график замирает вместе с биржей ─────────────────────────────────────
# Раннер строит бары из ленты, но когда лента молчит, подхватывает
# котировки-снимки и штампует плоские минуты замершей ценой. За ночь их набегает
# больше окна графика, и виджет показывает ровную нитку вместо последней живой
# сессии — график «идёт», хотя биржа стоит.

from trader.api.quik_companion import _live_bars, _tape_last_ms


def _bars(times):
    return [{"t": t, "o": 1, "h": 1, "l": 1, "c": 1} for t in times]


MIN = 60


def test_bars_after_the_last_trade_are_not_drawn():
    # последняя сделка в 10:00:30, дальше биржа стоит
    last = 10 * 3600 + 30
    got = _live_bars(_bars([9 * 3600, 9 * 3600 + MIN, 10 * 3600,
                            10 * 3600 + MIN, 10 * 3600 + 2 * MIN]), last * 1000)
    assert [b["t"] for b in got] == [9 * 3600, 9 * 3600 + MIN, 10 * 3600], \
        "минута со сделкой остаётся, ночные — нет"


def test_during_trading_nothing_is_cut():
    """Сделка секунду назад: свежий бар обязан остаться, иначе график мигает."""
    now = 12 * 3600
    got = _live_bars(_bars([now - 2 * MIN, now - MIN, now]), (now + 1) * 1000)
    assert len(got) == 3


def test_a_silent_minute_mid_session_is_synthetic_too():
    """Закрытая минута без единой сделки нарисована по замершей котировке.

    Соблазн дать допуск «на дребезг» есть, но хвост состоит только из ЗАКРЫТЫХ
    баров: раз минута закрылась позже последней сделки, сделок в ней не было —
    неважно, ночь это или тишина в неликвиде.
    """
    now = 12 * 3600
    got = _live_bars(_bars([now - MIN, now]), (now - 30) * 1000)
    assert [b["t"] for b in got] == [now - MIN]


def test_without_a_known_last_trade_we_do_not_cut_silently():
    bars = _bars([1, 2, 3])
    assert _live_bars(bars, None) == bars
    assert _live_bars(bars, 0) == bars


def test_whole_night_of_synthetic_bars_collapses_to_the_live_session():
    close = 23 * 3600 + 49 * MIN + 55          # последняя сделка вчера
    session = [close - i * MIN for i in range(29, -1, -1)]
    night = [close + i * MIN for i in range(1, 500)]   # 8 часов пустых минут
    got = _live_bars(_bars(session + night), close * 1000)
    assert len(got) == 30, "остаётся ровно последняя живая сессия"
    assert got[-1]["t"] <= close


# ── «Сделок не видели вовсе» — это не сделка секунду назад ────────────────────

def test_minus_one_lag_means_unknown_not_fresh():
    """exchange_lag_ms == -1 у агента значит «ни одной сделки не видели».

    Вычитание его как числа давало «последняя сделка = сейчас»: молчащая лента
    объявлялась самой свежей, часы биржи показывали текущее время, а обрезка
    ночных баров не срабатывала никогда. Поймано вживую 09.08 на закрытой бирже.
    """
    now = 1_786_000_000_000
    assert _tape_last_ms(now, -1) is None
    assert _tape_last_ms(now, None) is None
    assert _tape_last_ms(None, 5_000) is None
    assert _tape_last_ms(now, "мусор") is None


def test_real_lag_gives_the_trade_moment():
    now = 1_786_000_000_000
    assert _tape_last_ms(now, 0) == now
    assert _tape_last_ms(now, 13_288) == now - 13_288


def test_unknown_tape_does_not_silently_empty_the_chart():
    """Не знаем времени сделки — рисуем что есть, а не пустоту."""
    bars = [{"t": t, "o": 1, "h": 1, "l": 1, "c": 1} for t in (1, 2, 3)]
    assert _live_bars(bars, _tape_last_ms(1_786_000_000_000, -1)) == bars
