"""B2: возврат к VWAP дня - исполнительные алгоритмы и маркет-мейкеры.

Гипотеза и определения: docs/algo-footprints-registry.md, строка B2. VWAP дня
накопительный: VWAP[t] = sum(typical*vol)/sum(vol) по барам дня ДО t
ВКЛЮЧИТЕЛЬНО, typical=(h+l+c)/3. z[t] = (close[t]-VWAP[t]) / ATR_min
(common.atr_minute, весь ряд - как у A2, единый масштаб вместо шумного
дневного). Первые WARMUP_MIN=30 минут дня событий не дают (VWAP ещё не
сложился), но участвуют в отслеживании гистерезиса, чтобы не терять взвод на
границе окна.

Событие: |z| впервые >= k после того, как было < k/2 (гистерезис far/near, как
в b3.approaches). Исход на горизонте h: подписанный ход К VWAP
-sign(z[t])*(close[t+h]-close[t]) и признак touch - цена (по close) пересекла
или коснулась VWAP[t], уровня, ЗАСТЫВШЕГО в момент события (как уровень в b3,
а не едущая дальше линия).

Контроль "случайный якорь": тот же расчёт (тот же k, тот же гистерезис, тот же
h), но вместо ряда VWAP - ОДНО число: close случайного бара из первых
WARMUP_MIN минут дня, фиксированное на день в каждом розыгрыше (arg["draws"]
раз). Смысл контроля: если ход "к VWAP" не лучше хода "к случайному якорю",
цена просто возвращается к среднему (обычный mean reversion), а не тянется
конкретно к VWAP - это не эффект исполнительных алгоритмов.

Нестандартные решения:
- ATR минуты считается один раз по всему окну (не по дню) - короткий день даёт
  шумный ATR, а масштаб z должен быть сравним между днями и с VWAP-контролем.
  Меньше 60 баров в окне -> ATR не считается, модуль отдаёт пустой rows с
  пометкой в notes (событие без масштаба не определить, ноль вместо деления).
- Кандидаты случайного якоря - первые WARMUP_MIN минут дня (тот же порог, что
  и warmup для событий): якорь тоже должен быть "до того, как VWAP сложился".
- "Касание VWAP" проверяется против VWAP[t], уровня в момент события, НЕ
  против едущего дальше VWAP[t+1..t+h]. VWAP считается из цены самого ряда:
  если брать движущуюся линию, VWAP подтягивается к close на каждом баре
  (вес 1/cnt) и "касание" становится почти гарантированным даже на чистом
  блуждании без всякого притяжения - подмена доли подтверждений механическим
  артефактом self-tracking, а не эффектом возврата.

Событие не считается, если бар t+h выходит за пределы дня (недостаточный
форвард) - число отброшенных идёт в notes, не выбрасывается молча.

Сессия: бары вне 07:00-23:50 (common.session_rows) выброшены до разбивки по
dням, счётчик «до сессии»/«после сессии» в notes.
"""
from __future__ import annotations

import random
import statistics

from trader.lab.footprints import common

WARMUP_MIN = 30  # первые N минут дня: VWAP ещё не сложился, событий и якоря нет


def _day_vwap(day_rows: list[list]) -> list[float]:
    """Кумулятивный VWAP по бару дня, typical=(h+l+c)/3, накопление ДО t включительно."""
    out, cum_pv, cum_v = [], 0.0, 0.0
    for r in day_rows:
        typical = (r[2] + r[3] + r[4]) / 3.0
        cum_pv += typical * r[5]
        cum_v += r[5]
        out.append(cum_pv / cum_v if cum_v else r[4])  # ponytail: нулевой объём всего дня - вырожденный случай, close как есть
    return out


def _hysteresis_events(z: list[float], k: float, warmup: int) -> list[tuple[int, int]]:
    """Событие: |z| впервые >= k после того, как было < k/2 (одно на заход).

    Состояние обновляется и внутри warmup (иначе взвод на границе теряется),
    просто событие там не регистрируется.
    """
    events: list[tuple[int, int]] = []
    armed = True
    for i, zi in enumerate(z):
        az = abs(zi)
        if armed and az >= k:
            if i >= warmup:
                events.append((i, 1 if zi > 0 else -1))
            armed = False
        elif not armed and az < k / 2.0:
            armed = True
    return events


def _outcomes(day_rows: list[list], events: list[tuple[int, int, float]],
              h: int) -> tuple[list[float], int, int, int]:
    """Подписанный ход к уровню и признак touch в пределах h. events - тройки
    (индекс, знак, уровень), уровень ЗАСТЫВШИЙ в момент события (VWAP[t] или
    якорь). Возврат: (ходы, число касаний, число событий, число отброшенных
    за границу дня)."""
    moves: list[float] = []
    touches = n = dropped = 0
    for i, sign, level in events:
        if i + h >= len(day_rows):
            dropped += 1
            continue
        n += 1
        moves.append(-sign * (day_rows[i + h][4] - day_rows[i][4]))
        for j in range(i + 1, min(i + 1 + h, len(day_rows))):
            d = day_rows[j][4] - level
            if d == 0 or (d > 0) != (sign > 0):
                touches += 1
                break
    return moves, touches, n, dropped


def _control_null(days: dict, atr: float, k: float, h: int,
                   draws: int, rng: random.Random) -> tuple[list, list]:
    """Розыгрыши случайного якоря: медиана хода и доля touch за розыгрыш."""
    candidates = {d: list(range(min(WARMUP_MIN, len(blk)))) for d, blk in days.items()}
    null_move, null_touch = [], []
    for _ in range(draws):
        moves: list[float] = []
        touch_n = touch_t = 0
        for d, blk in days.items():
            cand = candidates[d]
            if not cand:
                continue
            anchor = blk[rng.choice(cand)][4]
            z = [(r[4] - anchor) / atr for r in blk]
            events = [(i, s, anchor) for i, s in _hysteresis_events(z, k, WARMUP_MIN)]
            mv, tc, n, _ = _outcomes(blk, events, h)
            moves.extend(mv)
            touch_n += n
            touch_t += tc
        null_move.append(statistics.median(moves) if moves else None)
        null_touch.append((touch_t / touch_n) if touch_n else None)
    return null_move, null_touch


def _k_h_row(days: dict, atr: float, k: float, h: int, draws: int,
             rng: random.Random, notes: list[str]) -> dict:
    real_by_day: dict = {}
    dropped_total = total_events = 0
    for d, blk in days.items():
        vwap = _day_vwap(blk)
        z = [(r[4] - v) / atr for r, v in zip(blk, vwap)]
        events = [(i, s, vwap[i]) for i, s in _hysteresis_events(z, k, WARMUP_MIN)]
        moves, touches, n, dropped = _outcomes(blk, events, h)
        real_by_day[d] = {"moves": moves, "touches": touches, "n": n}
        dropped_total += dropped
        total_events += n + dropped

    flat = [x for v in real_by_day.values() for x in v["moves"]]
    n_ev = len(flat)
    median = statistics.median(flat) if flat else None
    touch_n = sum(v["n"] for v in real_by_day.values())
    touch_t = sum(v["touches"] for v in real_by_day.values())
    touch_share = (touch_t / touch_n) if touch_n else None

    if dropped_total:
        notes.append(f"k={k:g} h={h}: горизонт за пределы дня, отброшено {dropped_total} из {total_events}")

    null_move, null_touch = _control_null(days, atr, k, h, draws, rng)

    def stat_move(blocks):
        vals = [x for b in blocks for x in b["moves"]]
        return statistics.median(vals) if vals else None

    def stat_touch(blocks):
        n_ = sum(b["n"] for b in blocks)
        t_ = sum(b["touches"] for b in blocks)
        return (t_ / n_) if n_ else None

    boot_move = common.bootstrap_days(real_by_day, stat_move, draws, rng)
    boot_touch = common.bootstrap_days(real_by_day, stat_touch, draws, rng)

    return {
        "k": k, "h": h, "n": n_ev,
        "median": median,
        "mean": statistics.fmean(flat) if flat else None,
        "pos_share": (sum(1 for x in flat if x > 0) / n_ev) if n_ev else None,
        "touch_share": touch_share,
        "null_move": common.pvalue_and_ci(median, null_move, boot_move),
        "null_touch": common.pvalue_and_ci(touch_share, null_touch, boot_touch),
    }


def _rows_for(days: dict, k_levels, horizons, draws: int, seed: int) -> tuple[list[dict], list[str]]:
    flat = sorted((r for blk in days.values() for r in blk), key=lambda r: r[0])
    atr = common.atr_minute(flat)
    notes: list[str] = [f"z = (close-VWAP)/ATR минуты всего окна, первые {WARMUP_MIN} мин "
                        "дня без событий (VWAP не сложился)"]
    if not atr:
        notes.append("ATR минуты не посчитан (< 60 баров в окне), события не ищутся")
        return [], notes
    rows = []
    for k in k_levels:
        rng = random.Random(seed * 1_000_003 + int(k * 100))
        for h in horizons:
            rows.append(_k_h_row(days, atr, float(k), int(h), draws, rng, notes))
    return rows, notes


def analyze(rows: list[list], k_levels=(2, 3, 4), horizons=(15, 30, 60),
            draws: int = 200, seed: int = 0) -> dict:
    rows, sess_notes = common.session_rows(rows)
    days = common.by_day(rows)
    real_rows, notes = _rows_for(days, k_levels, horizons, draws, seed)
    notes = sess_notes + notes
    first, second = common.halves(rows)
    first_rows, _ = _rows_for(common.by_day(first), k_levels, horizons, draws, seed) \
        if first else ([], [])
    second_rows, _ = _rows_for(common.by_day(second), k_levels, horizons, draws, seed) \
        if second else ([], [])
    return {
        "rows": real_rows,
        "halves": {"first": first_rows, "second": second_rows},
        "n_days": len(days),
        "notes": notes,
    }


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "since", "until", "k_levels", "horizons", "draws", "seed"}."""
    key, since, until = arg["symbol_key"], arg.get("since"), arg.get("until")
    rows = common.load_bars(key, since, until)
    if not rows:
        return {"id": "B2", "symbol": key, "window": [since, until], "error": "нет баров в окне"}
    res = analyze(rows,
                  tuple(arg.get("k_levels", (2, 3, 4))),
                  tuple(arg.get("horizons", (15, 30, 60))),
                  int(arg.get("draws", 200)), int(arg.get("seed", 0)))
    price = statistics.median(r[4] for r in rows)
    return common.report("B2", key, [since, until], res["rows"], res["notes"],
                         n_days=res["n_days"], halves=res["halves"],
                         cost_pts=common.round_trip_cost_pts(key, price),
                         atr_min_pts=common.atr_minute(rows))
