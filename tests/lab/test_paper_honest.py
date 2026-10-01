"""Честная бумажная наливка STL (honest_v1): встречная котировка или полспреда,
комиссия тейкера со скидкой скальпера как в бэктесте, решения по закрытому бару.

Аудит 01.10.2026: бумажные роботы STL наливались по close формирующегося бара ISS
без спреда и комиссии — +275 тыс ₽ на бумаге против −1.04..−1.42 млн при издержках.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from trader.lab import runtime as rtmod
from trader.lab.backtest import compute_metrics
from trader.lab.commission import commission_for
from trader.lab.runtime import Bar, LiveRuntime, _closed_bars, paper_commission

RI_META = {"price_step": 10, "price_step_value": 16.885, "point_value": 1.6885}
SI_META = {"price_step": 1, "price_step_value": 1.0, "point_value": 1.0}


class _Quotes:
    def __init__(self, bid, ask, age_ms=0):
        self.t = {"bid": bid, "ask": ask, "received_at_unix_ms": time.time() * 1000 - age_ms}

    def tick(self, code, agent_id=None):
        return self.t


@pytest.fixture(autouse=True)
def _no_quotes(monkeypatch):
    monkeypatch.setattr(rtmod, "_QUOTE_SOURCE", None)
    monkeypatch.delenv("LAB_PAPER_HONEST", raising=False)


def _rt(meta):
    rt = LiveRuntime("r1", None, paper=True)

    async def _meta(_sym):
        return meta
    rt._meta = _meta
    return rt


def _fill(rt, sym, side, price):
    return asyncio.run(rt.place_order(sym, side, 1, price))


def test_quote_counter_price():
    rtmod.set_quote_source(_Quotes(100.0, 110.0))
    rt = _rt(SI_META)
    buy = _fill(rt, "SiZ6", "buy", 105.0)
    sell = _fill(rt, "SiZ6", "sell", 105.0)
    assert (buy.price, sell.price) == (110.0, 100.0)
    assert ";honest_v1;quote;c=" in buy.order_id


def test_stale_quote_falls_back_to_estimate():
    rtmod.set_quote_source(_Quotes(100.0, 110.0, age_ms=11_000))
    assert _fill(_rt(SI_META), "SiZ6", "buy", 105.0).price == 105.5


def test_estimate_k_steps():
    rt = _rt(RI_META)
    assert _fill(rt, "RIZ6", "buy", 100000.0).price == 100007.0   # RI 0.7 шага
    o = _fill(rt, "RIZ6", "sell", 100000.0)
    assert o.price == 99993.0 and ";honest_v1;est;c=" in o.order_id
    assert _fill(_rt(SI_META), "SiZ6", "sell", 80000.0).price == 79999.5  # 0.5 шага


def test_switch_off_restores_old_fill(monkeypatch):
    monkeypatch.setenv("LAB_PAPER_HONEST", "0")
    o = _fill(_rt(RI_META), "RIZ6", "buy", 100000.0)
    assert o.price == 100000.0 and "honest" not in o.order_id


def test_broken_meta_never_drops_fill():
    o = _fill(_rt({"price_step": "abc"}), "RIZ6", "buy", 100000.0)
    assert o.status == "paper" and o.price == 100000.0 and "honest" not in o.order_id


def _ts(day, hour):
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc).timestamp()  # 30.09 = среда


@pytest.mark.parametrize("fills", [
    # внутридневной круг: скидка на обе ноги
    [("buy", 2, 100000.0, _ts(30, 11)), ("sell", 2, 100100.0, _ts(30, 15))],
    # усреднение и частичный выход внутри дня
    [("buy", 1, 100000.0, _ts(30, 11)), ("buy", 1, 99900.0, _ts(30, 12)),
     ("sell", 1, 100050.0, _ts(30, 13)), ("sell", 1, 100080.0, _ts(30, 14))],
    # ночёвка: без скидки
    [("sell", 3, 100000.0, _ts(29, 20)), ("buy", 3, 99800.0, _ts(30, 11))],
])
def test_commission_matches_backtest(fills):
    pv = 1.6885
    paid = sum(paper_commission("RIZ6", fills[:i], *f[:3], pv, f[3]) for i, f in enumerate(fills))
    trades = [{"side": s, "qty": q, "price": p, "time": ts} for s, q, p, ts in fills]
    gross = sum((p if s == "sell" else -p) * q for s, q, p, _ in fills) * pv
    net = compute_metrics(trades, 100000.0, point_value=pv, symbol="RIZ6")["net_profit"]
    assert paid == pytest.approx(gross - net, rel=1e-9)
    full = sum(commission_for("RIZ6", p, q, pv, taker=True, ts=ts) for _, q, p, ts in fills)
    same_day = len({int(f[3] // 86400) for f in fills}) == 1
    assert (paid < full - 1e-9) == same_day     # скидка есть ровно у внутридневного круга


def test_forming_bar_not_passed(monkeypatch):
    now = time.time()
    msk_min = int((now + 3 * 3600) // 60 * 60)            # начало текущей минуты, МСК-стенка
    bars = [Bar(msk_min - 120, 1, 1, 1, 1, 1), Bar(msk_min - 60, 2, 2, 2, 2, 1),
            Bar(msk_min, 3, 3, 3, 3, 1)]                   # последний ещё формируется
    assert [b.close for b in _closed_bars(bars, now)] == [1, 2]

    async def _load(symbol, days, interval):
        rtmod._BARS_FETCHED_AT[(symbol, days, interval)] = now
        return bars
    monkeypatch.setattr(rtmod, "_load_bars_shared", _load)
    rt = LiveRuntime("r1", None, paper=True)
    got = asyncio.run(rt.get_bars("RIZ6", 1, 10))
    assert got[-1].close == 2 and rt.newest_bar == msk_min - 60

    monkeypatch.setenv("LAB_PAPER_HONEST", "0")
    assert asyncio.run(LiveRuntime("r1", None, paper=True).get_bars("RIZ6", 1, 10))[-1].close == 3


class _Conn:
    def __init__(self, log):
        self.log = log

    async def execute(self, sql, *args):
        self.log.append((sql, args))

    async def fetch(self, sql, *args):
        return []

    async def fetchrow(self, sql, *args):
        return None


class _Pool:
    """Фейковый пул: считает INSERT в live_trades (ревью bb5f16f: бумажный филл
    перестал писаться в БД, а тесты с pool=None этого не видели)."""

    def __init__(self):
        self.log = []

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self_inner):
                return _Conn(pool.log)

            async def __aexit__(self_inner, *exc):
                return False
        return _Ctx()


@pytest.mark.parametrize("flag", ["1", "0"])
def test_paper_fill_is_recorded_once(monkeypatch, flag):
    monkeypatch.setenv("LAB_PAPER_HONEST", flag)
    pool = _Pool()
    rt = LiveRuntime("r1", pool, paper=True)

    async def _meta(_sym):
        return RI_META
    rt._meta = _meta
    order = asyncio.run(rt.place_order("RIZ6", "buy", 2, 85000.0))
    inserts = [a for s, a in pool.log if "INSERT INTO live_trades" in s]
    assert len(inserts) == 1
    args = inserts[0]
    # (id, robot_id, symbol, side, qty, price, order_id, status)
    assert args[1:5] == ("r1", "RIZ6", "buy", 2)
    assert float(args[5]) == order.price
    assert args[6] == order.order_id and args[7] == "paper"
    if flag == "1":
        assert ";honest_v1;" in args[6] and order.price == 85007.0
    else:
        assert ";honest_v1;" not in args[6] and order.price == 85000.0


def test_quote_age_prefers_stl_clock():
    """Свежесть по часам хостера: кадр с убежавшими вперёд часами VDS не считается
    свежим, если по stl_received_ms он старый; отрицательный возраст отвергается."""
    now_ms = time.time() * 1000

    class _Q:
        def __init__(self, tick):
            self._t = tick

        def tick(self, code, agent_id=None):
            return self._t

    stale_by_stl = {"bid": 100.0, "ask": 110.0,
                    "received_at_unix_ms": now_ms + 60_000, "stl_received_ms": now_ms - 30_000}
    rtmod.set_quote_source(_Q(stale_by_stl))
    assert LiveRuntime._fresh_quote("SiZ6") is None
    fresh_by_stl = {"bid": 100.0, "ask": 110.0,
                    "received_at_unix_ms": now_ms - 600_000, "stl_received_ms": now_ms - 1_000}
    rtmod.set_quote_source(_Q(fresh_by_stl))
    assert LiveRuntime._fresh_quote("SiZ6") == (100.0, 110.0)
    future_only_vds = {"bid": 100.0, "ask": 110.0, "received_at_unix_ms": now_ms + 60_000}
    rtmod.set_quote_source(_Q(future_only_vds))
    assert LiveRuntime._fresh_quote("SiZ6") is None
