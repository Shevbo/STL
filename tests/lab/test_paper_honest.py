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
