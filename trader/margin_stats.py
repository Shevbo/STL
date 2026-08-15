"""Статистика множителя брокера над биржевым ГО.

ЗАЧЕМ. Биржевое ГО инструмента известно из фида, а счёт платит другое: 30.07.2026
на RIU6 биржа требовала 22 375 ₽, а со счёта списывалось 53 672 ₽ — множитель 2.4.
Это число живёт в `QUIK_MARGIN_MULTIPLIER` КОНСТАНТОЙ, и на неё опираются и отчёт
компаньона, и карточка робота, и отбор кандидатов: строка, чья лестница «влезает
в счёт», проверяется именно через него. Оператор (15.08.2026) заметил, что
множитель, похоже, гуляет по времени дня и на ралли. Пока он константа, все эти
проверки врут ровно на величину ошибки — поэтому его надо не угадывать, а МЕРИТЬ.

КАК СЧИТАЕТСЯ. Агент отдаёт занятое ГО счёта (`cbplused`, «Тек. чист. поз.») и
позиции по инструментам, а фид — биржевое ГО контракта. Тогда

    множитель = занятое ГО счёта / Σ |позиция| × биржевое ГО

Замер годен, только когда сошлось ВСЁ: связь есть, деньги свежие, и биржевое ГО
известно для КАЖДОГО инструмента с ненулевой позицией. Одна неизвестная бумага —
и знаменатель занижен, а множитель завышен на ровном месте. Такой замер мы не
пишем вовсе: пустая статистика честнее засорённой.

ЧЕГО ЭТО НЕ МЕРЯЕТ. Занятое ГО — это ВЕСЬ счёт, включая ручные позиции оператора.
Отделить их нельзя и не нужно: множитель — свойство счёта, а не робота.
"""
from __future__ import annotations

from dataclasses import dataclass

# Санитарные границы. Множитель ниже единицы означает, что счёт платит МЕНЬШЕ
# биржи, — так не бывает, значит мы неверно поняли одно из чисел. Сверху 10 —
# заведомо абсурд даже для самого жадного риск-менеджмента. За границами замер
# отбрасывается: одна кривая точка портит медиану сильнее, чем её отсутствие.
MULT_MIN = 1.0
MULT_MAX = 10.0

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS margin_multiplier_samples (
    ts_ms          BIGINT NOT NULL,
    used_rub       DOUBLE PRECISION NOT NULL,
    exchange_rub   DOUBLE PRECISION NOT NULL,
    multiplier     DOUBLE PRECISION NOT NULL,
    positions      JSONB NOT NULL,
    PRIMARY KEY (ts_ms)
);
CREATE INDEX IF NOT EXISTS idx_margin_mult_ts ON margin_multiplier_samples(ts_ms DESC);
"""


@dataclass(frozen=True)
class Sample:
    ts_ms: int
    used_rub: float
    exchange_rub: float
    multiplier: float
    positions: dict


def compute(ts_ms: int, used_rub: float | None, positions: dict,
            exchange_margin: dict) -> Sample | None:
    """Один замер или None, если считать не из чего.

    positions: {код: нетто-позиция со знаком}. exchange_margin: {код: ГО биржи}.
    """
    try:
        used = float(used_rub or 0)
    except (TypeError, ValueError):
        return None
    if used <= 0:
        return None                    # позиций нет — множителю неоткуда взяться
    denom = 0.0
    kept: dict = {}
    for code, pos in (positions or {}).items():
        try:
            n = abs(int(pos))
        except (TypeError, ValueError):
            continue
        if not n:
            continue
        m = exchange_margin.get(code)
        try:
            m = float(m or 0)
        except (TypeError, ValueError):
            m = 0.0
        if m <= 0:
            return None                # ГО этой бумаги неизвестно — замер негоден
        denom += n * m
        kept[code] = int(pos)
    if denom <= 0:
        return None
    mult = used / denom
    if not (MULT_MIN <= mult <= MULT_MAX):
        return None
    return Sample(ts_ms=int(ts_ms), used_rub=round(used, 2),
                  exchange_rub=round(denom, 2), multiplier=round(mult, 4),
                  positions=kept)


def summarize(samples: list[dict]) -> dict:
    """Сводка по замерам: сколько, разброс и медиана — общая и по часам МСК.

    Медиана, а не среднее: один замер в момент смены клиринга уводит среднее, а
    вопрос оператора («гуляет ли множитель по времени дня») требует устойчивой
    середины, иначе любой выброс читается как режим дня.
    """
    vals = sorted(float(s["multiplier"]) for s in samples if s.get("multiplier"))
    if not vals:
        return {"n": 0, "median": None, "min": None, "max": None, "by_hour": {}}

    def _med(xs: list[float]) -> float:
        n = len(xs)
        mid = n // 2
        return round(xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2, 4)

    by_hour: dict[int, list[float]] = {}
    for s in samples:
        m = s.get("multiplier")
        ts = s.get("ts_ms")
        if not m or not ts:
            continue
        # МСК = UTC+3 без перехода на летнее время.
        by_hour.setdefault(int(((ts // 1000) + 3 * 3600) // 3600 % 24), []).append(float(m))
    return {
        "n": len(vals), "median": _med(vals), "min": round(vals[0], 4),
        "max": round(vals[-1], 4),
        "by_hour": {h: {"n": len(v), "median": _med(sorted(v))}
                    for h, v in sorted(by_hour.items())},
    }
