"""Вход us_open_fvg считается по ФАКТУ позиции, а не по отправке заявки.

Инцидент 16.09.2026 на РЕАЛЬНОМ agent-usopen-RIU6-v1 (письмо real-trade): лимитка на
вход не налилась, раннер снял её перед следующим баром, но состояние уже стояло
entered=1 при нулевой позиции — робот считал день отторгованным и простоял его весь.
В бэктесте это не видно: там заявка исполняется всегда.
"""
import asyncio

from trader.lab.runtime import BacktestRuntime
from trader.lab.strategies.us_open_fvg import on_bar
from tests.lab.test_usopen_invert import SYM, _day_bars

PARAMS = {"symbol": SYM, "qty": 1, "entry_mode": 0, "req_fvg": 1, "min_frac": 5,
          "range_min": 5, "signal_min": 60, "rr_x10": 20, "stop_pct": 0,
          "open_hour": 16, "open_min": 30}


class NoFillRuntime(BacktestRuntime):
    """Как живой раннер: заявка уходит, но не исполняется и снимается перед баром."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.sent = []

    async def place_order(self, symbol, side, qty, price):
        self.sent.append((side, qty, price))
        return None


def _run(rt) -> None:
    while True:
        asyncio.run(on_bar(rt, PARAMS))
        if not rt.advance():
            break


def test_unfilled_entry_does_not_burn_the_day():
    rt = NoFillRuntime(bars=_day_bars(), symbol=SYM, initial_equity=1_000_000.0)
    _run(rt)
    assert len(rt.sent) > 1, "неисполненный вход сжёг день: робот больше не пытался войти"
    assert not rt.get_state("entered"), "вход записан при нулевой позиции"


def test_filled_entry_marks_entered_and_trades_once():
    rt = BacktestRuntime(bars=_day_bars(), symbol=SYM, initial_equity=1_000_000.0)
    _run(rt)
    assert rt.get_state("entered") == 1
    entries = [o for o in rt._orders if o.side == "buy"]
    assert len(entries) == 1, f"вход должен быть один, а их {len(entries)}"


def test_position_from_previous_day_is_still_closed():
    """Страховка от осиротевшей позиции обязана пережить правку: позиция без входа
    закрывается, иначе она копится день за днём."""
    rt = BacktestRuntime(bars=_day_bars(), symbol=SYM, initial_equity=1_000_000.0)
    rt._positions[SYM] = {"side": "long", "qty": 3, "avg": 100.0}
    asyncio.run(on_bar(rt, PARAMS))
    assert rt._orders and rt._orders[0].side == "sell" and rt._orders[0].qty == 3
    assert rt.get_state("done") == 1
