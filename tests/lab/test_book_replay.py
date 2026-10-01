"""Исполнение по архивному стакану: проход по уровням, сдвиг времени, откат к бару."""
import asyncio
import json

from trader.lab.book_replay import MSK_SHIFT, BookRuntime, load_book
from trader.lab.runtime import Bar


def _bars(n=10, start=1_700_000_000, price=100.0):
    return [Bar(start + 60 * i, price, price + 1, price - 1, price, 10) for i in range(n)]


def _write(tmp_path, rows):
    p = tmp_path / "book-2026-09-16.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return [str(p)]


def test_load_applies_msk_shift(tmp_path):
    utc_ms = 1_789_516_800_000
    files = _write(tmp_path, [{"code": "RIU6", "received_at_unix_ms": str(utc_ms),
                               "bids": [{"price": 99.0, "quantity": "5"}],
                               "asks": [{"price": 101.0, "quantity": "5"}]}])
    times, books = load_book(files, "RIU6")
    assert times == [utc_ms // 1000 + MSK_SHIFT]
    assert books[0][0][0] == (99.0, 5) and books[0][1][0] == (101.0, 5)


def test_load_skips_other_code_and_empty_side(tmp_path):
    files = _write(tmp_path, [
        {"code": "SiU6", "received_at_unix_ms": "1000", "bids": [{"price": 1, "quantity": "1"}],
         "asks": [{"price": 2, "quantity": "1"}]},
        {"code": "RIU6", "received_at_unix_ms": "1000", "bids": [], "asks": []},
    ])
    assert load_book(files, "RIU6") == ([], [])


def test_walk_vwap_and_depth():
    lv = [(100.0, 2), (101.0, 3)]
    assert BookRuntime._walk(lv, 2) == (100.0, False)
    price, deep = BookRuntime._walk(lv, 5)
    assert abs(price - (2 * 100 + 3 * 101) / 5) < 1e-9 and deep is False
    price, deep = BookRuntime._walk(lv, 6)          # глубины не хватило
    assert deep is True and price > 100.0


def test_buy_takes_asks_sell_takes_bids():
    bars = _bars()
    t = bars[6].time                                 # открытие следующего бара после курсора 5
    book = ([t], [([(99.0, 10)], [(101.0, 1), (103.0, 10)])])
    rt = BookRuntime(bars=bars, symbol="RIU6", initial_equity=0.0, book=book)
    rt._cursor = 5
    buy = asyncio.run(rt.place_order("RIU6", "buy", 3, 0))
    assert abs(buy.fill_price - (101 + 2 * 103) / 3) < 1e-9
    rt._cursor = 5
    sell = asyncio.run(rt.place_order("RIU6", "sell", 1, 0))
    assert sell.fill_price == 99.0
    assert rt.stats["book"] == 2 and rt.stats["no_book"] == 0
    assert rt.stats["slip_pts"] > 0                  # спред всегда против нас


def test_without_snapshot_falls_back_to_bar_open():
    bars = _bars()
    rt = BookRuntime(bars=bars, symbol="RIU6", initial_equity=0.0, book=([], []))
    rt._cursor = 5
    o = asyncio.run(rt.place_order("RIU6", "buy", 1, 0))
    assert o.fill_price == bars[6].open and rt.stats["no_book"] == 1 and rt.stats["book"] == 0


def test_stale_snapshot_counts_as_no_book():
    """Снимок старше порога свежести = стакана нет: иначе замер мерит ход рынка за
    трое суток вместо спреда (провалы архива при рестарте STL)."""
    bars = _bars()
    t = bars[6].time
    book = ([t + 3600], [([(99.0, 10)], [(101.0, 10)])])    # час позже заявки
    rt = BookRuntime(bars=bars, symbol="RIU6", initial_equity=0.0, book=book, max_gap_s=60)
    rt._cursor = 5
    o = asyncio.run(rt.place_order("RIU6", "buy", 1, 0))
    assert o.fill_price == bars[6].open and rt.stats["no_book"] == 1
    rt2 = BookRuntime(bars=bars, symbol="RIU6", initial_equity=0.0, book=book, max_gap_s=7200)
    rt2._cursor = 5
    assert asyncio.run(rt2.place_order("RIU6", "buy", 1, 0)).fill_price == 101.0


def _touch_rt(mode, snaps, **kw):
    """Курсор 5, заявка исполняется в t = открытие бара 6; snaps = [(dt, asks)]."""
    bars = _bars()
    t = bars[6].time
    book = ([t + dt for dt, _a in snaps], [([(99.0, 10)], asks) for _dt, asks in snaps])
    rt = BookRuntime(bars=bars, symbol="RIU6", initial_equity=0.0, book=book, exec_mode=mode, **kw)
    rt._cursor = 5
    return rt


def test_touch_vs_walk_qty_above_best():
    """qty 10 при 3 на best: walk = VWAP прохода; touch = 3 по best сразу, остаток
    доливается по правилу touch_fill до следующего бара."""
    snaps = [(0, [(101.0, 3), (103.0, 10)]),
             (10, [(101.0, 4), (103.0, 10)]),          # best восстановился на нашей цене
             (20, [(100.0, 5), (102.0, 10)])]          # рынок прошёл наш уровень
    walk = _touch_rt("walk", snaps)
    assert abs(asyncio.run(walk.place_order("RIU6", "buy", 10, 0)).fill_price
               - (3 * 101 + 7 * 103) / 10) < 1e-9

    for fill, late_fills in (("opt", [4, 3]), ("pess", [7])):
        rt = _touch_rt("touch", snaps, touch_fill=fill)
        first = asyncio.run(rt.place_order("RIU6", "buy", 10, 0))
        assert first.fill_price == 101.0 and first.qty == 3
        assert rt._positions["RIU6"]["qty"] == 3          # до следующего бара висит остаток
        assert rt.advance()
        assert [o.qty for o in rt._orders[1:]] == late_fills, fill
        assert all(o.fill_price == 101.0 for o in rt._orders)
        assert rt._positions["RIU6"] == {"side": "long", "qty": 10, "avg": 101.0}
        assert rt.stats["touch_rest"] == 7 and rt.stats["touch_late"] == 7


def test_touch_unfilled_cancel_or_market():
    """Стакан стоит, pess не доливает: cancel оставляет 3 лота, market добивает 7
    проходом по снимку конца окна (ttl 60 с)."""
    snaps = [(0, [(101.0, 3), (103.0, 10)]), (30, [(101.0, 3), (103.0, 10)]),
             (60, [(102.0, 10)])]
    rt = _touch_rt("touch", snaps, touch_fill="pess")
    asyncio.run(rt.place_order("RIU6", "buy", 10, 0))
    rt.advance()
    assert rt._positions["RIU6"]["qty"] == 3 and rt.stats["touch_unfilled"] == 7
    assert rt._pending == []

    rt = _touch_rt("touch", snaps, touch_fill="pess", touch_ttl_action="market")
    asyncio.run(rt.place_order("RIU6", "buy", 10, 0))
    rt.advance()
    assert rt._orders[-1].qty == 7 and rt._orders[-1].fill_price == 102.0
    assert rt._positions["RIU6"]["qty"] == 10 and rt.stats["touch_market"] == 7
