"""Моменты, когда инструмент был РОВНО в нуле.

Зачем. Сумма журнала сделок равна позиции счёта только тогда, когда окно
журнала начинается с ФЛЭТА по инструменту. Нашего такого начала нет: журнал
ведётся с 23.09.2026, а позиция набиралась и раньше, поэтому 02.10.2026
расхождение «журнал +3 контракта против счёта» не удалось ни подтвердить, ни
опровергнуть — момента нуля не хранил никто (real-trade: «у меня тоже нет»).
Дальше так оставаться не должно: ноль по инструменту виден в ту же секунду, и
записав его, мы делаем окно журнала СЧИТАЕМЫМ без чьей-либо памяти.

Три условия, на которых запись честна (согласованы с real-trade 02.10.2026):

1. пишем ТОЛЬКО из свежего кадра с зелёным линком. Старый кадр с нулями значит
   «не знаю», а не «флэт», и спутать эти два ответа здесь дороже всего: ложный
   ноль сделает «считаемым» окно, которое считать нельзя;
2. в строке — возраст кадра и build_rev агента, чтобы потом было видно, на чём
   основан этот ноль;
3. один момент на ПЕРЕХОД в ноль, а не каждый кадр: час флэта иначе дал бы
   тысячи строк об одном и том же событии.

Источник — `data/truth.json`, тот же снимок, по которому работает scripts/pos.py:
правда о позициях у нас одна, и читать её вторым способом значило бы завести
второй ответ на тот же вопрос.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

PATH = Path(os.environ.get("STL_FLAT_MARKS", "data/flat_moments.jsonl"))
TRUTH = Path(os.environ.get("STL_TRUTH_PATH", "data/truth.json"))
#: кадр старше этого — «не знаю», даже если в нём ноль
MAX_AGE_MS = 10_000
POLL_SEC = 5.0


def frame_usable(truth: dict[str, Any]) -> str:
    """Пустая строка — кадру верим; иначе причина, по которой не верим."""
    if not truth:
        return "кадра нет"
    if truth.get("stale"):
        return str(truth.get("stale_why") or "кадр помечен устаревшим")
    age = truth.get("pos_age_ms")
    if age is None:
        return "возраст позиций неизвестен"
    if float(age) >= MAX_AGE_MS:
        return f"позиции старше {MAX_AGE_MS} мс ({float(age):.0f})"
    link = truth.get("link_age_ms")
    if link is None:
        return "возраст линка неизвестен"
    if float(link) >= MAX_AGE_MS:
        return f"линк молчит {float(link):.0f} мс"
    return ""


def transitions(truth: dict[str, Any], seen: dict[str, bool],
                build_rev: Any = None) -> list[dict[str, Any]]:
    """Строки для записи и ОБНОВЛЕНИЕ `seen` на месте.

    `seen[sec] is True` — инструмент уже был в нуле на прошлом годном кадре.
    Негодный кадр не трогает память вовсе: он не доказывает ни нуля, ни выхода
    из него, а «забыв» ноль по молчанию зеркала, мы записали бы его второй раз.
    """
    if frame_usable(truth):
        return []
    out: list[dict[str, Any]] = []
    for p in truth.get("positions") or []:
        sec = str(p.get("sec") or "")
        if not sec:
            continue
        try:
            flat = int(p.get("net") or 0) == 0
        except (TypeError, ValueError):
            continue
        was = seen.get(sec)
        seen[sec] = flat
        # Пишем только ПЕРЕХОД в ноль. Первый кадр после старта (was is None) с
        # уже нулевой позицией переходом не считаем: когда она обнулилась, мы не
        # знаем, а время записи выдало бы это за момент сделки.
        if flat and was is False:
            out.append({
                "ts_ms": int(truth.get("ts_ms") or 0),
                "sec": sec,
                "pos_age_ms": int(float(truth.get("pos_age_ms") or 0)),
                "link_age_ms": int(float(truth.get("link_age_ms") or 0)),
                "build_rev": build_rev,
            })
    return out


def _build_rev(app_state: Any) -> Any:
    store = getattr(app_state, "quik_store", None)
    if store is None:
        return None
    try:
        for st in store.status():
            rev = (st.get("register") or {}).get("build_rev")
            if rev:
                return rev
    except Exception:  # noqa: BLE001 — ревизия справочная, её отсутствие не повод молчать о флэте
        return None
    return None


def append(lines: list[dict[str, Any]]) -> None:
    if not lines:
        return
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with PATH.open("a", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(json.dumps(ln, ensure_ascii=False) + "\n")


async def run(app_state: Any) -> None:
    """Фоновый опрос снимка. Своих данных не держит — читает файл правды."""
    seen: dict[str, bool] = {}
    while True:
        try:
            raw = TRUTH.read_text(encoding="utf-8")
            lines = transitions(json.loads(raw), seen, _build_rev(app_state))
            if lines:
                append(lines)
                for ln in lines:
                    log.info("flat_mark", **ln)
        except FileNotFoundError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — сторож не имеет права ронять API
            log.warning("flat_marks.failed", error=str(exc), exc_type=type(exc).__name__)
        await asyncio.sleep(POLL_SEC)
