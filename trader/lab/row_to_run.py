"""Строка лидерборда -> готовый вектор параметров для ОДИНОЧНОГО прогона.

ЗАЧЕМ. Экран «Прогоны» показывает строки кампаний, у которых нет ни сделок, ни
кривой: API хранит их только у лучшей комбинации прогона. Чтобы оператор увидел
сделки строки, её надо досчитать одиночным прогоном ТЕМ ЖЕ вектором. Собирать
вектор руками из `params` нельзя: окно ui-ux прямо написало, что однажды соберёт
неверно, а это прогон не того, что показано на экране, то есть ложь на экране.

ЧТО ЗДЕСЬ РЕШАЕТСЯ, кроме копирования словаря:
- служебные ключи (`symbol`, `book_key`) не параметры робота: символ прогона
  берётся ИЗ СТРОКИ отдельным полем, а `book_key` в одиночном прогоне не нужен;
- схема стратегии живёт в коде и меняется: у старой строки могут быть ключи,
  которых в схеме уже нет, и наоборот — новые оси, которых в строке нет. Молча
  подставлять дефолты опасно: дефолт нового `flip_close_loss` = 0 это ЗАПРЕТ
  закрывать убыток, то есть другое поведение, чем было при прогоне строки;
- типы: в JSON числа приезжают float, а движок половину осей читает как int.

Поэтому функция НИЧЕГО не выдумывает, а возвращает вектор + список расхождений.
Вызывающая сторона решает, показывать ли предупреждение (и обязана показать).

    from trader.lab.row_to_run import row_to_run
    job = row_to_run(row)          # row = строка optimization_leaderboard
    job.params                     # вектор для paramSets
    job.symbol, job.date_from      # окно и символ ИЗ СТРОКИ, не с экрана
    job.warnings                   # что разошлось со схемой; пусто = чисто
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SERVICE_KEYS = ("symbol", "book_key")


@dataclass
class RunSpec:
    params: dict[str, Any]
    symbol: str
    date_from: str | None
    date_to: str | None
    strategy: str
    warnings: list[str] = field(default_factory=list)

    def to_job(self) -> dict[str, Any]:
        """Тело для POST /api/v1/backtest/run (одиночный прогон, ручной приоритет)."""
        return {"scriptCode": ("from trader.lab.strategies.library import make_on_bar\n"
                               f"on_bar = make_on_bar({self.strategy!r})"),
                "baseParams": dict(self.params),
                "paramSets": [{}],
                "symbol": self.symbol,
                "dateFrom": f"{self.date_from}T00:00:00" if self.date_from else None,
                "dateTo": f"{self.date_to}T23:59:59" if self.date_to else None,
                "engine": "remote"}


def _schema(strategy: str) -> dict[str, dict]:
    from trader.lab.strategies.library import REGISTRY
    entry = REGISTRY.get(strategy) or {}
    return {s["key"]: s for s in (entry.get("params_schema") or [])}


def row_to_run(row: dict[str, Any]) -> RunSpec:
    """Собрать вектор одиночного прогона из строки лидерборда.

    `row` — словарь со полями строки: strategy, symbol, params, date_from, date_to.
    `params` может быть уже распарсенным словарём или JSON-строкой.
    """
    import json

    strategy = str(row.get("strategy") or "")
    raw = row.get("params")
    if isinstance(raw, str):
        raw = json.loads(raw)
    raw = dict(raw or {})

    symbol = str(raw.get("symbol") or row.get("symbol") or "")
    if not symbol:
        raise ValueError("в строке нет символа: прогон не на чем ставить")
    if not strategy:
        raise ValueError("в строке нет стратегии")

    schema = _schema(strategy)
    warnings: list[str] = []
    params: dict[str, Any] = {}

    for k, v in raw.items():
        if k in SERVICE_KEYS:
            continue
        spec = schema.get(k)
        if spec is None:
            # Ключ из прошлой версии схемы. Оси уже нет, движок её не читает —
            # тащить в прогон бессмысленно, но промолчать нельзя: строка считалась
            # ДРУГИМ кодом, и её результат этим прогоном может не повториться.
            warnings.append(f"ключ {k} не из текущей схемы {strategy}, выброшен")
            continue
        if spec.get("type") == "number" and isinstance(v, float) and v.is_integer():
            v = int(v)
        params[k] = v

    for k, spec in schema.items():
        if k in params or k in SERVICE_KEYS:
            continue
        # Ось появилась ПОСЛЕ прогона строки. Подставляем дефолт (иначе движок
        # возьмёт его сам), но обязательно предупреждаем: у новой оси дефолт
        # может менять поведение, как flip_close_loss=0 = запрет закрывать убыток.
        params[k] = spec.get("default")
        warnings.append(f"ось {k} появилась после прогона, взят дефолт "
                        f"{spec.get('default')!r}")

    params["symbol"] = symbol
    return RunSpec(params=params, symbol=symbol,
                   date_from=str(row["date_from"])[:10] if row.get("date_from") else None,
                   date_to=str(row["date_to"])[:10] if row.get("date_to") else None,
                   strategy=strategy, warnings=warnings)
