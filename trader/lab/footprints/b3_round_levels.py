"""B3: пиннинг и отскок у круглых уровней против псевдоуровней.

Гипотеза и определения: docs/algo-footprints-registry.md, строка B3. Уровни —
кратные step круглые цены (по умолчанию из DEFAULT_STEPS по префиксу
symbol_key); контроль — тот же расчёт на псевдоуровнях: L' ровно между
круглыми (шаг/2, `null_half`) и L'' со случайным сдвигом (0.1..0.9)*step,
arg["draws"] розыгрышей (`null_random`).

eps = 0.5*ATR_min, d = 2*ATR_min (`common.atr_minute`, весь ряд). Событие
«подход»: бар входит в eps от ближайшего уровня впервые после того, как был
дальше d — гистерезис far/near держит «один заход = одно событие», не по
бару у уровня (см. `approaches`). Исход (bounce/break/none) меряется по close
в следующие arg["horizon"] баров и не переходит границу дня (`_step_rows`
работает по дневным блокам).

Нестандартное решение: спецификация даёт одно поле `null_random` на строку, но
регистр явно просит нулевое распределение и для P(отскок), и для доли
пиннинга — поэтому `null_random` = {"bounce": {...}, "pinning": {...}}, каждое
формы `common.pvalue_and_ci`.
"""
from __future__ import annotations

import random
import statistics

from trader.lab.footprints import common

DEFAULT_STEPS: dict[str, tuple[float, ...]] = {
    "RI": (500, 1000),
    "SI": (500, 1000),
    "GD": (10, 50),
    "BR": (0.5, 1),
}


def steps_for(symbol_key: str, steps=None) -> tuple[float, ...]:
    """arg["steps"] как есть, иначе DEFAULT_STEPS по префиксу symbol_key."""
    if steps:
        return tuple(steps)
    from trader.lab.commission import _base_ticker
    d = DEFAULT_STEPS.get(_base_ticker(symbol_key))
    if not d:
        raise ValueError(f"arg['steps'] обязателен для {symbol_key}: нет значения по умолчанию")
    return d


def _nearest_level(price: float, step: float, offset: float = 0.0) -> float:
    k = round((price - offset) / step)
    return k * step + offset


def approaches(day_rows: list[list], step: float, offset: float,
               eps: float, d: float) -> list[tuple[int, float, str]]:
    """События подхода за один день: (индекс бара, уровень, направление).

    Гистерезис far/near: событие только при переходе far->near (было дальше d
    от текущего уровня, теперь в пределах eps); пока near, дальнейшие бары у
    того же уровня новых событий не дают, см. докстринг модуля.
    """
    events: list[tuple[int, float, str]] = []
    near = False
    tracked = None
    prev_price = None
    for i, r in enumerate(day_rows):
        price = r[4]
        if not near:
            lvl = _nearest_level(price, step, offset)
            if abs(price - lvl) <= eps:
                if prev_price is not None:
                    direction = "below" if prev_price < lvl else "above" if prev_price > lvl else None
                    if direction:
                        events.append((i, lvl, direction))
                near, tracked = True, lvl
        elif abs(price - tracked) > d:
            near, tracked = False, None
        prev_price = price
    return events


def _outcome(day_rows: list[list], i: int, level: float, direction: str,
             d: float, horizon: int) -> str:
    """bounce/break/none по close в следующие horizon баров, в пределах дня."""
    away = level - d if direction == "below" else level + d
    through = level + d if direction == "below" else level - d
    for j in range(i + 1, min(i + 1 + horizon, len(day_rows))):
        price = day_rows[j][4]
        if (price <= away) if direction == "below" else (price >= away):
            return "bounce"
        if (price >= through) if direction == "below" else (price <= through):
            return "break"
    return "none"


def _rate_stats(blocks: list[list], step: float, offset: float, eps: float, d: float,
                horizon: int, direction: str) -> dict:
    n = bounce = brk = none_ = 0
    for day_rows in blocks:
        for i, lvl, dirn in approaches(day_rows, step, offset, eps, d):
            if dirn != direction:
                continue
            n += 1
            outcome = _outcome(day_rows, i, lvl, dirn, d, horizon)
            if outcome == "bounce":
                bounce += 1
            elif outcome == "break":
                brk += 1
            else:
                none_ += 1
    return {"n": n, "p_bounce": bounce / n if n else None,
            "p_break": brk / n if n else None, "p_none": none_ / n if n else None}


def _pinning(rows: list[list], step: float, offset: float, eps: float) -> float | None:
    if not rows:
        return None
    hits = sum(1 for r in rows if abs(r[4] - _nearest_level(r[4], step, offset)) <= eps)
    return hits / len(rows)


def _step_rows(rows: list[list], steps, eps: float, d: float, horizon: int,
               draws: int, seed: int) -> list[dict]:
    days = common.by_day(rows)
    blocks = list(days.values())
    if not blocks:
        return []
    rng = random.Random(seed)
    out = []
    for step in steps:
        for direction in ("below", "above"):
            real = _rate_stats(blocks, step, 0.0, eps, d, horizon, direction)
            real["pinning_share"] = _pinning(rows, step, 0.0, eps)

            half = _rate_stats(blocks, step, step / 2.0, eps, d, horizon, direction)
            half["pinning_share"] = _pinning(rows, step, step / 2.0, eps)

            null_bounce, null_pin = [], []
            for _ in range(draws):
                off = (0.1 + 0.8 * rng.random()) * step
                null_bounce.append(_rate_stats(blocks, step, off, eps, d, horizon, direction)["p_bounce"])
                null_pin.append(_pinning(rows, step, off, eps))

            boot_bounce = common.bootstrap_days(
                days, lambda bl, s=step, dr=direction: _rate_stats(bl, s, 0.0, eps, d, horizon, dr)["p_bounce"],
                draws, rng)
            boot_pin = common.bootstrap_days(
                days, lambda bl, s=step: _pinning([r for blk in bl for r in blk], s, 0.0, eps),
                draws, rng)

            out.append({
                "step": step, "direction": direction,
                "n": real["n"], "p_bounce": real["p_bounce"],
                "p_break": real["p_break"], "p_none": real["p_none"],
                "pinning_share": real["pinning_share"],
                "null_half": half,
                "null_random": {
                    "bounce": common.pvalue_and_ci(real["p_bounce"], null_bounce, boot_bounce),
                    "pinning": common.pvalue_and_ci(real["pinning_share"], null_pin, boot_pin),
                },
            })
    return out


def analyze(rows: list[list], steps, horizon: int = 30, draws: int = 200, seed: int = 0) -> dict:
    atr = common.atr_minute(rows)
    eps = 0.5 * atr if atr else 0.0
    d = 2.0 * atr if atr else 0.0
    first, second = common.halves(rows)
    notes = [f"eps={eps:.3g} пт, d={d:.3g} пт (0.5x/2x ATR минуты)" if atr
             else "ATR минуты не посчитан (< 60 баров в окне), eps=d=0"]
    return {
        "rows": _step_rows(rows, steps, eps, d, horizon, draws, seed),
        "halves": {"first": _step_rows(first, steps, eps, d, horizon, draws, seed),
                   "second": _step_rows(second, steps, eps, d, horizon, draws, seed)},
        "n_days": len(common.by_day(rows)),
        "notes": notes,
    }


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "since", "until", "steps", "horizon", "draws", "seed"}."""
    key, since, until = arg["symbol_key"], arg.get("since"), arg.get("until")
    rows = common.load_bars(key, since, until)
    if not rows:
        return {"id": "B3", "symbol": key, "window": [since, until], "error": "нет баров в окне"}
    steps = steps_for(key, arg.get("steps"))
    res = analyze(rows, steps, int(arg.get("horizon", 30)), int(arg.get("draws", 200)),
                  int(arg.get("seed", 0)))
    price = statistics.median(r[4] for r in rows)
    return common.report("B3", key, [since, until], res["rows"], res["notes"],
                         n_days=res["n_days"], halves=res["halves"],
                         cost_pts=common.round_trip_cost_pts(key, price),
                         atr_min_pts=common.atr_minute(rows))
