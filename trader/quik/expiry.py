"""Следующий контракт серии для перекладки на экспирации (docs/design/expiry-roll.md).

ЗАЧЕМ ОТДЕЛЬНО ОТ trader/lab/contract_roll.front_contract. Тот отвечает на вопрос
«какой контракт серии торгуется сейчас» и включает в кандидаты СЕГОДНЯШНИЙ последний
торговый день. В день экспирации это ровно тот контракт, с которого мы уезжаем: 16.09
у RIU6 LASTTRADEDATE = 17.09, и 17-го front_contract вернул бы снова RIU6, то есть
кампания переложила бы робота саму на себя.

Здесь вопрос другой: «куда переезжать с ЭТОГО контракта». Ответ - ближайший контракт
той же серии, чей последний торговый день СТРОГО ПОЗЖЕ, чем у текущего. Отсюда же
следует правильное поведение для месячных серий (BR): BRV6 -> BRX6, а не сразу BRZ6.

Данные - ISS (единственный источник дат экспирации). Разбор строк вынесен в чистые
функции: тесты не ходят в сеть, а кампания получает те же ответы на фиксированных
данных.
"""
from __future__ import annotations

from datetime import date, datetime

from trader.lab.contract_roll import _securities, base_of

__all__ = ["base_of", "expiry_of", "next_contract", "resolve_next"]


def _as_date(s: str) -> date | None:
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def expiry_of(symbol: str, rows: list[tuple[str, str]]) -> date | None:
    """Последний торговый день контракта по строкам ISS (SECID, LASTTRADEDATE)."""
    want = (symbol or "").strip().lower()
    for sid, ltd in rows:
        if str(sid).strip().lower() == want:
            return _as_date(ltd)
    return None


def next_contract(symbol: str, rows: list[tuple[str, str]],
                  today: date | None = None) -> str | None:
    """Контракт той же серии, на который переезжаем с `symbol`.

    Берём ближайший по дате контракт серии, чей последний торговый день СТРОГО позже,
    чем у `symbol`. Если самого `symbol` в выдаче ISS уже нет (контракт истёк и снят с
    торгов, как BRU6 после 01.09), точкой отсчёта становится `today`: переезжаем на
    ближайший ещё живой контракт серии.
    """
    base = base_of(symbol)
    if base is None:
        return None
    pivot = expiry_of(symbol, rows)
    if pivot is None:
        pivot = (today or date.today())
        strictly_after = False        # истёкший контракт: годится и сегодняшний срок
    else:
        strictly_after = True
    cands: list[tuple[date, str]] = []
    for sid, ltd in rows:
        if base_of(sid) != base or str(sid).strip().lower() == (symbol or "").strip().lower():
            continue
        d = _as_date(ltd)
        if d is None:
            continue
        if d > pivot or (not strictly_after and d >= pivot):
            cands.append((d, sid))
    if not cands:
        return None
    return min(cands)[1]


async def resolve_next(symbol: str, today: date | None = None) -> str | None:
    """next_contract по живой выдаче ISS. None, если ISS недоступен или серия неизвестна."""
    try:
        rows = await _securities()
    except Exception:  # noqa: BLE001 — нет связи с ISS: кампания скажет об этом в чекапе
        return None
    return next_contract(symbol, rows, today)
