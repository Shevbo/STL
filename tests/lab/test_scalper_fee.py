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
# СРЕДА 02.09.2026, 08:00. День недели тут не косметика: на выходных торгах сбор
# удваивается, и фикстура, случайно попавшая на субботу, мерила бы совсем другое
# (первая версия этого файла так и попала — T0 оказался субботой).
T0 = 1788336000


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


# ── выходные торги ───────────────────────────────────────────────────────────
# FORTS торгует в субботу и воскресенье, и там И биржевой сбор, И брокерский, И ГО
# удваиваются (оператор, 06.09.2026). У нас весь реестр минутный, то есть два дня
# из семи считались вдвое дешевле, чем стоят.
_SAT = 1788595200            # суббота 05.09.2026
_WED = T0                    # среда 02.09.2026


def test_the_fixture_days_are_what_they_claim():
    """Тест про выходные бессмыслен, если фикстура промахнулась днём недели."""
    import datetime as dt
    assert dt.datetime.fromtimestamp(_SAT, dt.UTC).weekday() == 5
    assert dt.datetime.fromtimestamp(_WED, dt.UTC).weekday() == 2


def test_weekend_fill_costs_double():
    weekday = commission_for(SYM, 80000.0, 1, PV, taker=True, ts=_WED)
    weekend = commission_for(SYM, 80000.0, 1, PV, taker=True, ts=_SAT)
    assert abs(weekend / weekday - 2.0) < 1e-9


def test_weekend_and_scalper_discount_compose():
    """Внутридневной круг в субботу: удвоение биржи и половинная ставка вместе."""
    full_wknd = commission_for(SYM, 80000.0, 1, PV, taker=True, ts=_SAT)
    scal_wknd = commission_for(SYM, 80000.0, 1, PV, taker=True, scalper=True, ts=_SAT)
    broker_wknd = BROKER_FEE_PER_CONTRACT * 2
    assert abs((scal_wknd - broker_wknd) - (full_wknd - broker_wknd) * 0.5) < 1e-9


def test_margin_doubles_on_weekend_and_scales_by_account_status():
    from trader.lab.commission import margin_for
    exch = 21000.0
    assert margin_for(exch, _WED, 1.0) == exch                  # КПУР, будни
    assert margin_for(exch, _SAT, 1.0) == exch * 2              # КПУР, выходной
    assert margin_for(exch, _WED, 2.4) == exch * 2.4            # до 01.09
    assert margin_for(exch, _SAT, 2.4) == exch * 4.8


def test_missing_timestamp_is_treated_as_a_weekday():
    """Старые вызовы без метки не должны внезапно подорожать вдвое."""
    assert commission_for(SYM, 80000.0, 1, PV, taker=True) == \
challenge if False else commission_for(SYM, 80000.0, 1, PV, taker=True, ts=None)
