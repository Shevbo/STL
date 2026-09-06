"""Скальперская скидка биржи: круг внутри сессии платит бирже вдвое меньше.

Найдено окном real-trade 05.09.2026 сверкой факта QUIK против модели за две недели
на RIU6: доля 0.44-0.61 в оборотистые дни против 1.07 в тихий, когда позиция
ночевала. ISS публикует обе ставки, и SCALPERFEE ровно половина BUYSELLFEE на
каждом проверенном контракте.

Тест пинит ровно то, что было сломано: одинаковую цену круга независимо от того,
закрылся он в тот же день или назавтра.
"""
from trader.lab.backtest import compute_metrics
from trader.lab.commission import BROKER_FEE_PER_CONTRACT, commission_for

SYM, PV = "RIU6", 1.7
DAY = 86400
T0 = 1788000000 // DAY * DAY + 8 * 3600      # утро условной сессии


def _pair(exit_ts: int) -> float:
    """Чистый результат круга вход-выход по одной цене: это МИНУС комиссия."""
    trades = [{"side": "buy", "price": 80000.0, "qty": 1, "time": T0},
              {"side": "sell", "price": 80000.0, "qty": 1, "time": exit_ts}]
    return compute_metrics(trades, 100000.0, point_value=PV, symbol=SYM)["net_profit"]


def test_intraday_round_trip_pays_half_the_exchange_fee():
    intraday = -_pair(T0 + 3 * 3600)          # закрылись в тот же день
    overnight = -_pair(T0 + 30 * 3600)        # переночевали
    broker = 2 * BROKER_FEE_PER_CONTRACT      # брокеру скидки нет
    exch_over = overnight - broker
    exch_intra = intraday - broker
    assert exch_over > 0
    assert abs(exch_intra / exch_over - 0.5) < 1e-6, (intraday, overnight)


def test_overnight_is_unchanged_from_the_flat_model():
    """Позиция, которая ночевала, обязана стоить ровно столько же, сколько раньше:
    иначе правка задним числом переписала бы все прежние строки лидерборда."""
    overnight = -_pair(T0 + 30 * 3600)
    both_legs = 2 * commission_for(SYM, 80000.0, 1, PV, taker=True)
    assert abs(overnight - both_legs) < 1e-6


def test_scalper_flag_touches_only_the_exchange_part():
    full = commission_for(SYM, 80000.0, 3, PV, taker=True)
    half = commission_for(SYM, 80000.0, 3, PV, taker=True, scalper=True)
    broker = BROKER_FEE_PER_CONTRACT * 3
    assert abs((half - broker) - (full - broker) * 0.5) < 1e-9
    assert half > broker            # биржевая часть не обнулилась
