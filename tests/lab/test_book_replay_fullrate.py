"""Режим «касание» на i9: мс-выжимка стакана, ключи исполнения в наборе, потолок заданий."""
import argparse
import asyncio
import importlib.util
import pathlib

from trader.lab.book_replay import BookRuntime, load_digest
from trader.lab.runtime import Bar


def _load(name):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(f"scripts/{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


oa = _load("opt_agent")
pps = _load("post_param_sets")

T0 = 1_790_000_000


def _row(ts, bid, ask):
    return [ts] + [x for i in range(5) for x in (bid - i, 3)] + [x for i in range(5) for x in (ask + i, 3)]


def test_ms_digest_equals_seconds_digest():
    sec = [_row(T0 + 60 * i, 100 + i, 101 + i) for i in range(5)]
    ms = [[r[0] * 1000] + r[1:] for r in sec]
    assert load_digest(ms, ts_unit="ms") == load_digest(sec)


def test_ms_digest_keeps_subsecond_and_lag_lookup():
    """Снимки 1.2 с: метки дробные, lag 5 с находит снимок не позже t - 5."""
    rows = [_row(T0 * 1000 + 1200 * k, 100, 101 + k) for k in range(100)]
    times, books = load_digest(rows, ts_unit="ms")
    assert abs(times[1] - T0 - 1.2) < 1e-6 and len(set(times)) == 100
    bars = [Bar(T0 + 60 * i, 100.0, 101.0, 99.0, 100.0, 10) for i in range(3)]
    rt = BookRuntime(bars=bars, symbol="RIZ6", initial_equity=0.0, book=(times, books),
                     exec_mode="touch", quote_lag_s=5)
    rt._cursor = 0
    o = asyncio.run(rt.place_order("RIZ6", "buy", 1, 0))
    # t0 = 60.0 (снимок 50), t0 - 5 = 55.0 -> последний снимок не позже = 45 (54.0 с)
    assert o.price == 101 + 45 and o.status == "submitted"
    rt.advance()                                    # best дальше только растёт: не долилось
    assert rt.stats["touch_unfilled"] == 1 and all(isinstance(x.fill_time, int) for x in rt._orders)


def test_pop_book_params_strips_every_set():
    sets = [{"a": 1, "book_key": "bookRIZ6f0929", "book_exec_mode": "touch",
             "book_touch_fill": "opt", "book_quote_lag_s": 5, "book_touch_ttl_action": "cancel"},
            {"a": 2, "book_key": "bookRIZ6f0929", "book_exec_mode": "touch"}]
    key, opts = oa._pop_book_params(sets)
    assert key == "bookRIZ6f0929"
    assert opts == {"exec_mode": "touch", "touch_fill": "opt", "quote_lag_s": 5,
                    "touch_ttl_action": "cancel"}
    assert sets == [{"a": 1}, {"a": 2}]


def test_old_job_runtime_kw_unchanged():
    sec = [_row(T0 + 60 * i, 100, 101) for i in range(3)]
    key, opts = oa._pop_book_params([{"a": 1, "book_key": "bookRIZ6d0929"}])
    assert opts == {}
    kw = oa._book_runtime_kw({"rows": sec, "ts_unit": "s", "opts": opts})
    assert kw == {"book": load_digest(sec), "max_gap_s": 60}
    kw = oa._book_runtime_kw({"rows": [[r[0] * 1000] + r[1:] for r in sec], "ts_unit": "ms",
                              "opts": {"exec_mode": "touch"}})
    assert kw["book"] == load_digest(sec) and kw["exec_mode"] == "touch"


def _ns(**kw):
    d = {"book_key": None, "book_exec_mode": None, "book_touch_fill": None,
         "book_quote_lag_s": None, "book_touch_ttl_action": None}
    return argparse.Namespace(**{**d, **kw})


def test_post_param_sets_base_and_cap():
    base = {}
    assert pps.apply_book_args(base, _ns()) is None and base == {}
    base = {}
    assert pps.apply_book_args(base, _ns(book_key="bookRIZ6d0929")) == pps.BOOK_CHUNK
    assert base == {"book_key": "bookRIZ6d0929"}
    base = {}
    cap = pps.apply_book_args(base, _ns(book_key="bookRIZ6f0929", book_exec_mode="touch",
                                        book_touch_fill="opt", book_quote_lag_s=5.0,
                                        book_touch_ttl_action="cancel"))
    assert cap == pps.BOOK_FULL_CHUNK == 20
    assert base == {"book_key": "bookRIZ6f0929", "book_exec_mode": "touch",
                    "book_touch_fill": "opt", "book_quote_lag_s": 5.0,
                    "book_touch_ttl_action": "cancel"}
    assert pps.apply_book_args({}, _ns(book_key="bookRIZ6d0929", book_exec_mode="touch")) == 20
