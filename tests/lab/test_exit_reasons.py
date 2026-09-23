"""Причины выхода считаются и доезжают до результата бэктеста.

ЗАЧЕМ. Заказ real-trade 23.09.2026: по живой торговле тейк lxk22 стоит в 391 п.
при медиане лучшего хода 119 п., и за 22-23.09 ни один из 41 цикла до тейка не
дошёл — закрывал разворот сигнала. Вопрос «участвует ли тейк вообще» описательный:
он решается распределением причин выхода, а не статистикой по деньгам.

Проверяем сквозняк: движок размечает выход, бэктест отдаёт exit_reasons.
"""
import asyncio

from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import Bar
from trader.lab.strategies import library


def _bars(n: int = 400) -> list[Bar]:
    """Пила с растущей амплитудой: даёт и тейки, и развороты сигнала."""
    out, px, t = [], 100000.0, 1_700_000_000
    for i in range(n):
        px += (120.0 if (i // 25) % 2 == 0 else -120.0)
        out.append(Bar(time=t + i * 60, open=px, high=px + 40, low=px - 40,
                       close=px, volume=100))
    return out


def test_exit_reasons_reach_the_result():
    mod = type("M", (), {"on_bar": library.make_on_bar("macd_shectory1")})
    params = {"qty": 1, "fast": 5, "slow": 12, "signal": 4, "avg_atr_n": 14,
              "tp_atr": 20, "sl_pct": 100, "avg_step_atr": 0, "avg_max": 1,
              "flatten_end": 1, "symbol": "TEST"}
    res = asyncio.run(run_single_backtest(mod, _bars(), "TEST", params,
                                          point_value=1.0))
    reasons = res.get("exit_reasons") or {}
    assert reasons, f"причины не доехали: ключи результата {sorted(res)[:12]}"
    # Ключи только из известного набора — опечатка в разметке выхода видна сразу.
    assert set(reasons) <= {"tp", "sl", "flip", "layer", "back", "other"}, reasons
    # Сумма причин не больше числа закрытых кругов (последний выход может быть
    # принудительным закрытием в конце окна, его стратегия не размечает).
    assert sum(reasons.values()) >= 1
    assert sum(reasons.values()) <= res["total_trades"] + 1, (reasons, res["total_trades"])
