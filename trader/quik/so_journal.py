"""Журнал событий ручных (умных) заявок: что, когда и ПО ЧЬЕЙ воле случилось.

ЗАЧЕМ. Книга `data/smart_orders.json` хранит ТЕКУЩЕЕ состояние заявки: сработала,
снята, осиротела. Историю она затирает — заявка, которая утром жила под охраной
терминала, днём вернулась к сторожу STL, а вечером была снята оператором, выглядит
в книге одной строкой «снята». Восстанавливать путь приходилось по journalctl, где
записи живут до ротации и перемешаны с остальным STL (23.09.2026 так разбирали
исчезнувшую продажу 30 контрактов).

Теперь каждое событие ложится строкой в `data/so_events/YYYY-MM-DD.jsonl`:

    {"ts_ms":…, "so_id":"2b2990d2c4", "event":"fired", "source":"сторож STL",
     "code":"RIZ6", "side":"sell", "qty":20, "detail":"по 85620, пик 85840"}

ИСТОЧНИК — главное поле. У ручной заявки три распорядителя, и путать их нельзя:
оператор (поставил, снял), сторож STL (следит по ленте и стреляет) и терминал QUIK
(держит нативную стоп-заявку и исполняет её сам, даже когда STL лежит). «Заявка
снята» без источника не отвечает на единственный важный вопрос — снял её человек
или она отвалилась сама.

Запись не имеет права мешать торговле: ошибка файловой системы гасится и логируется,
торговый проход продолжается.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

DIR = "data/so_events"
MSK_OFFSET_MS = 3 * 3600 * 1000

# Распорядители заявки. Строки идут в интерфейс как есть — на русском, потому что
# читает их оператор, а не программа.
OPERATOR = "оператор"
WATCHER = "сторож STL"
TERMINAL = "терминал QUIK"
LIMITS = "лимиты STL"


def day_path(ts_ms: int, directory: str | None = None) -> str:
    directory = directory or DIR
    day = time.strftime("%Y-%m-%d", time.gmtime((ts_ms + MSK_OFFSET_MS) / 1000))
    return os.path.join(directory, f"{day}.jsonl")


def record(event: str, so: Any, source: str, detail: str = "",
           now_ms: int | None = None, directory: str | None = None) -> None:
    """Одна строка в суточный журнал. Никогда не бросает.

    `directory` берётся ПО ВЫЗОВУ, а не защёлкивается в дефолте аргумента: тесты
    подменяют DIR на временную папку, иначе прогон сеет журналы в репозиторий."""
    directory = directory or DIR
    ts = now_ms or int(time.time() * 1000)
    row = {
        "ts_ms": ts, "so_id": getattr(so, "so_id", ""), "event": event,
        "source": source, "kind": getattr(so, "kind", ""),
        "code": getattr(so, "code", ""), "side": getattr(so, "side", ""),
        "qty": getattr(so, "qty", 0), "status": getattr(so, "status", ""),
        "parent_id": getattr(so, "parent_id", ""), "detail": detail,
    }
    try:
        os.makedirs(directory, exist_ok=True)
        with open(day_path(ts, directory), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("so_journal.write_failed", error=str(exc), event=event)


def read_days(days: list[str], directory: str | None = None) -> list[dict[str, Any]]:
    """События за перечисленные МСК-даты, по времени. Нет файла — нет событий."""
    directory = directory or DIR
    out: list[dict[str, Any]] = []
    for day in days:
        path = os.path.join(directory, f"{day}.jsonl")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue      # оборванная строка не повод потерять журнал
    out.sort(key=lambda r: int(r.get("ts_ms") or 0))
    return out


def coverage(directory: str | None = None) -> str:
    """Самая ранняя дата, за которую журнал вообще есть. Пусто — журнала нет.

    Экран обязан её показывать: «за месяц» по журналу, начатому неделю назад, —
    это не месяц, и выдавать одно за другое нельзя."""
    try:
        days = sorted(f[:-6] for f in os.listdir(directory or DIR) if f.endswith(".jsonl"))
    except OSError:
        return ""
    return days[0] if days else ""
