"""D2/D3 на синтетике (реальных данных здесь нет, STRICT: считает только i9).

ОБЪЁМ ДАННЫХ. Задание просило 4 часа при ~5 сделок/с (~72k сделок); при таком
объёме D2/D3 с draws>=100 (нужно для p<0.01, см. common.pvalue_and_ci) не
укладываются в pytest.ini timeout=30 на тест (замер: 4ч/draws=120/1 горизонт
> 30 с). Окна здесь укорочены до 2 часов (~36k сделок, по-прежнему ~5/с) --
замер 10-13 с на тест, статистика та же.

big_q ПОВЫШЕН до 0.999 в тестах (2)/(3): при geom(p=0.4) естественный 99-й
перцентиль объёма (~q=0.99, дефолт run()) уже держит ~1% сделок дня -- на 36k
сделок это ~360 "крупных" НЕ по замыслу, они топят медианный эффект введённых
40 событий в шуме. 0.999 оставляет в выборке практически только вставленные
события (объём x50 недостижим случайным геометрическим хвостом)."""
import json
import math
import random
from datetime import datetime, timezone

from trader.lab.footprints import common
from trader.lab.footprints import d23_tape_flow as d23

D0 = int(datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc).timestamp())  # 10:00, до 16:00 -> одна половина


def _geom(rng: random.Random, p: float = 0.4) -> int:
    """Геометрическое число >=1 (метод обратной функции распределения)."""
    u = rng.random()
    return max(1, int(math.log(1 - u) / math.log(1 - p)) + 1)


def _row(res, test, h):
    return next(r for r in res["rows"] if r["test"] == test and r["h"] == h)


def _tape_random(hours=2, seed=1) -> list[list]:
    """Случайное блуждание, случайные стороны, ~5 сделок/с, объём геометрический."""
    rng = random.Random(seed)
    price, rows = 100000.0, []
    for s in range(hours * 3600):
        for _ in range(rng.randint(3, 7)):
            price += rng.gauss(0, 0.4)
            rows.append([D0 + s, round(price, 3), _geom(rng), 1 if rng.random() < 0.5 else 0])
    return rows


def _tape_big_print(hours=2, seed=2, big_every=180, mult=50, bump=3.0, ramp_s=10) -> list[list]:
    """Как _tape_random, плюс раз в big_every c крупная покупка (qty x mult) и
    следующие ramp_s секунд цена детерминированно растёт на bump пт суммарно."""
    rng = random.Random(seed)
    price, rows = 100000.0, []
    ramp_left, step = 0, 0.0
    for s in range(hours * 3600):
        if ramp_left > 0:
            price += step
            ramp_left -= 1
        for k in range(rng.randint(3, 7)):
            price += rng.gauss(0, 0.3)
            qty = _geom(rng)
            buy = rng.random() < 0.5
            if s > 0 and s % big_every == 0 and k == 0:
                qty *= mult
                buy = True
                ramp_left, step = ramp_s, bump / ramp_s
            rows.append([D0 + s, round(price, 3), qty, 1 if buy else 0])
    return rows


def _tape_ofi(hours=2, seed=7, every=600, bump=6.0, ramp_s=60) -> list[list]:
    """Как _tape_random, плюс раз в every секунд минута со сдвигом сторон к
    покупке (OFI сильно положительный), затем следующие ramp_s (=60, минута)
    секунд цена детерминированно растёт на bump пт суммарно."""
    rng = random.Random(seed)
    price, rows = 100000.0, []
    ramp_left, step = 0, 0.0
    for s in range(hours * 3600):
        if ramp_left > 0:
            price += step
            ramp_left -= 1
        minute_start = (s // 60) * 60
        signal = every and minute_start % every == 0 and minute_start > 0
        for _ in range(rng.randint(3, 7)):
            price += rng.gauss(0, 0.3)
            qty = _geom(rng)
            p_buy = 0.9 if signal else 0.5
            rows.append([D0 + s, round(price, 3), qty, 1 if rng.random() < p_buy else 0])
        if signal and s % 60 == 59:
            ramp_left, step = ramp_s, bump / ramp_s
    return rows


def test_random_walk_no_effect():
    """(1) Случайное блуждание, случайные стороны -> D2 и D3 не значимы."""
    res = d23.analyze(_tape_random(), big_q=0.999, horizons_s=(10, 60), draws=50, seed=0)
    assert _row(res, "D2", 10)["null"]["p"] > 0.05
    assert _row(res, "D3", 60)["null_corr"]["p"] > 0.05
    assert _row(res, "D3", 60)["null_move"]["p"] > 0.05


def test_big_print_drift_detected():
    """(2) ~40 крупных покупок (объём x50), после каждой цена растёт на 3 пт
    за 10 с -> D2 p<0.01 при h=10."""
    res = d23.analyze(_tape_big_print(), big_q=0.999, horizons_s=(10,), draws=120, seed=0)
    row = _row(res, "D2", 10)
    assert row["n_big"] >= 30
    assert row["median_signed"] > 1.5
    assert row["null"]["p"] < 0.01
    # контроль (медианный размер, те же минуты) не должен нести того же эффекта
    assert row["control_median"] < row["median_signed"]


def test_ofi_predicts_next_minute():
    """(3) Минуты с большим положительным OFI сопровождаются ростом цены в
    следующую минуту -> D3 p<0.01 при h=1 мин (h=60 с)."""
    res = d23.analyze(_tape_ofi(), big_q=0.999, horizons_s=(60,), draws=120, seed=0)
    row = _row(res, "D3", 60)
    assert row["median_signed_top_decile"] > 2.0
    assert row["null_move"]["p"] < 0.01
    assert row["corr"] > 0


def test_prepare_ms_and_side_buy_fallback():
    """ts_ms -> ts_s; side_buy=None -> сторона по тику (>= предыдущей = покупка)."""
    data = {"ts_unit": "ms", "side_buy": None,
            "rows": [[1000, 100.0, 1, 9], [2000, 99.0, 1, 9], [2000, 101.0, 1, 9]]}
    rows, notes = d23._prepare(data)
    assert rows == [[1, 100.0, 1, 1], [2, 99.0, 1, 0], [2, 101.0, 1, 1]]
    assert any("тику" in n for n in notes)


def test_report_json_safe():
    res = d23.analyze(_tape_random(hours=1, seed=5), horizons_s=(10, 60), draws=10)
    rep = common.report("D23", "RIZ6", ["2026-09-01", "2026-09-01"], res["rows"], res["notes"],
                        n_days=res["n_days"], halves=res["halves"],
                        cost_pts=common.round_trip_cost_pts("RIZ6"), atr_min_pts=1.0)
    json.dumps(rep, allow_nan=False)
    assert set(rep) == {"id", "symbol", "window", "n_days", "rows", "halves",
                        "cost_pts", "atr_min_pts", "notes"}
