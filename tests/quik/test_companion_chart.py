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
