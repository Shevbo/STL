"""Карантин после слепоты: не исполнять сигнал, родившийся в дыре.

ЗАЧЕМ. 25.09.2026 STL три часа не видел рынка — агент был отключён, данных не
было. Пока мы были слепы, цена прошла уровень активации взведённой заявки. Связь
вернулась в 16:02, и в 16:13 сторож честно, по своим правилам, купил 20
контрактов RIZ6 — по уровню, пройденному в дыре, в тот момент, когда оператор в
дороге уже набирал позицию руками и не мог ни увидеть её, ни оценить.

Формально сторож сработал верно. Фактически он исполнил намерение, принятое
оператором при других обстоятельствах и устаревшее за три часа тишины.

ПРАВИЛО. После перерыва в данных дольше `BLIND_SEC` взведённые заявки не стреляют
ещё `QUARANTINE_SEC` — пауза на то, чтобы человек увидел картину и снял лишнее.

КОГО КАРАНТИН НЕ КАСАЕТСЯ: защитных заявок (у них есть parent_id — это стоп и
тейк на УЖЕ ОТКРЫТОЙ позиции). Задержать вход — потерять возможность; задержать
выход — оставить позицию голой, и это дороже. Карантин держит ВХОДЫ.
"""

from __future__ import annotations

from typing import Any

BLIND_SEC = 120          # перерыв в данных дольше этого = мы были слепы
QUARANTINE_SEC = 180     # столько после возвращения входы не стреляют


class BlindQuarantine:
    """Состояние сторожа: когда данные пропадали и когда вернулись."""

    def __init__(self, blind_sec: int = BLIND_SEC, quarantine_sec: int = QUARANTINE_SEC):
        self.blind_sec = blind_sec
        self.quarantine_sec = quarantine_sec
        self._last_seen_ms = 0          # последний проход со СВЕЖИМИ данными
        self._until_ms = 0              # до какого момента входы под карантином
        self._gap_sec = 0               # длина дыры, из-за которой он взведён

    def observe(self, fresh: bool, now_ms: int) -> None:
        """Отметить проход сторожа. `fresh` — есть ли живой кадр рынка."""
        if not fresh:
            return
        if self._last_seen_ms:
            gap = (now_ms - self._last_seen_ms) / 1000
            if gap >= self.blind_sec:
                self._gap_sec = int(gap)
                self._until_ms = now_ms + self.quarantine_sec * 1000
        self._last_seen_ms = now_ms

    def active(self, now_ms: int) -> bool:
        return now_ms < self._until_ms

    def left_sec(self, now_ms: int) -> int:
        return max(0, int((self._until_ms - now_ms) / 1000))

    @property
    def gap_sec(self) -> int:
        return self._gap_sec

    def clear(self) -> None:
        """Снять карантин досрочно (оператор посмотрел и разрешил)."""
        self._until_ms = 0


def holds(order: Any) -> bool:
    """Держит ли карантин ЭТУ заявку.

    Защитные (с родителем) не держим никогда: они закрывают открытую позицию, и
    их задержка оставляет её без защиты."""
    return not getattr(order, "parent_id", "")
