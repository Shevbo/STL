"""Запись сырого рынка на диск: стакан, тики, жизненный цикл заявок, отказы.

ЗАЧЕМ. Бэктестер сегодня исполняет по close минутного бара, стакана у него нет
вовсе (BacktestRuntime.get_orderbook возвращает пустые списки), спред и
проскальзывание не моделируются. Из-за этого целый класс стратегий непроверяем, а
проверяемый систематически оптимистичен. Всё нужное агент УЖЕ шлёт в STL — просто
никто это не сохранял, и каждый день данные умирали в памяти.

ИЗОЛЯЦИЯ (главное требование). Сбор не имеет права сломать торговлю:
  • в торговом пути ровно один вызов record(), он никогда не бросает;
  • запись идёт в фоновом таске через очередь, приём кадров её не ждёт;
  • очередь ОГРАНИЧЕНА: при переполнении кадры молча теряются, а не копятся;
  • любая ошибка файловой системы гасит запись до конца дня, торговля продолжается;
  • по умолчанию ВЫКЛЮЧЕНО, включается QUIK_RECORD_DIR.

ФОРМАТ. JSONL + gzip, файл на (дату, тип). Не бинарь и не БД намеренно: pandas
читает напрямую, gzip даёт ~10x на однородных строках, а таблица на миллионы
строк в сутки уже роняла нам снапшот компаньона (21.6 с на запрос).

ГЕЙТ ПО СОДЕРЖИМОМУ обязателен. Lua переставляет метку времени стакана каждую
секунду, даже когда сам стакан не менялся: гейт по времени пропустит всё, гейт по
содержимому оставит только реальные изменения. Тот же урок уже стоил нам x5 CPU
на пересылке (см. «Agent flush discipline» в CLAUDE.md).
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger()

# Очередь ограничена: сбор данных НИКОГДА не должен создавать давление на приём
# кадров. Полная очередь = теряем кадры и считаем их в dropped, а не тормозим
# торговый путь.
_QUEUE_MAX = 20_000
_FLUSH_EVERY = 200          # строк между flush: компромисс потери и сисколлов


class MarketRecorder:
    """Пишет кадры агента на диск. Все методы безопасны в торговом пути."""

    def __init__(self, root: str | None = None) -> None:
        self.root = root or os.environ.get("QUIK_RECORD_DIR") or ""
        self.enabled = bool(self.root)
        self._q: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self._files: dict[str, Any] = {}
        self._day: str = ""
        self._since_flush = 0
        # Гейт по содержимому: последний ЗАПИСАННЫЙ снимок на инструмент.
        self._last: dict[str, str] = {}
        self.stats = {"written": 0, "dropped": 0, "skipped_same": 0, "errors": 0}

    # ---- торговый путь: только это и вызывается ----

    # Какие кадры агента идут в архив. Остальные (heartbeat, diagnostics, статусы
    # роботов) для моделирования исполнения бесполезны и только раздули бы файлы.
    KINDS = {
        "order_book": "book",              # стакан: спред, глубина, очередь
        "tick": "tick",                    # last/bid/ask + открытый интерес
        "order_update": "order",           # жизненный цикл НАШЕЙ заявки
        "trans_reply": "reply",            # отказы QUIK: бэктест исполняет всегда
        "execution_update": "exec",        # частичные заливки
    }

    def record_frame(self, field: str | None, msg) -> None:
        """Единственная точка входа из торгового пути. Конвертирует protobuf сам."""
        if not self.enabled:
            return
        kind = self.KINDS.get(field or "")
        if kind is None:
            return
        if self._q is None:
            # Ленивый старт с первого же интересного кадра. Так подключение архива
            # не требует правки lifespan в trader/api (чужая зона) и не добавляет
            # ещё одну фоновую задачу в приложение, когда запись выключена.
            self._lazy_start()
            if self._q is None:
                return
        try:
            from google.protobuf.json_format import MessageToDict
            payload = MessageToDict(getattr(msg, field), preserving_proto_field_name=True)
        except Exception:  # noqa: BLE001 — кадр архива не стоит сбоя приёма
            self.stats["errors"] += 1
            return
        self.record(kind, payload)

    def record(self, kind: str, payload: dict) -> None:
        """Положить кадр в очередь. НИКОГДА не бросает и никогда не блокирует."""
        if not self.enabled or self._q is None:
            return
        try:
            # Гейт по содержимому для стакана и тиков: писать только изменения.
            if kind in ("book", "tick"):
                key = f"{kind}:{payload.get('code', '')}"
                sig = self._signature(kind, payload)
                if sig is not None and self._last.get(key) == sig:
                    self.stats["skipped_same"] += 1
                    return
                if sig is not None:
                    self._last[key] = sig
            self._q.put_nowait((kind, payload))
        except asyncio.QueueFull:
            self.stats["dropped"] += 1
        except Exception:  # noqa: BLE001 — запись данных не стоит ни одного сбоя торговли
            self.stats["errors"] += 1

    @staticmethod
    def _signature(kind: str, p: dict) -> str | None:
        """Отпечаток СОДЕРЖИМОГО без метки времени: она переставляется всегда."""
        try:
            if kind == "book":
                return json.dumps([p.get("bids"), p.get("asks")], separators=(",", ":"))
            return json.dumps([p.get("last"), p.get("bid"), p.get("ask"),
                               p.get("open_interest")], separators=(",", ":"))
        except Exception:  # noqa: BLE001
            return None

    # ---- фон ----

    def _lazy_start(self) -> None:
        """Поднять очередь и писателя внутри уже работающего цикла событий."""
        try:
            os.makedirs(self.root, exist_ok=True)
            self._q = asyncio.Queue(maxsize=_QUEUE_MAX)
            self._task = asyncio.create_task(self._run(), name="market-recorder")
            log.info("recorder.started", root=self.root)
        except Exception as exc:  # noqa: BLE001 — нет каталога/прав -> просто не пишем
            log.warning("recorder.start_failed", root=self.root, error=str(exc))
            self.enabled = False
            self._q = None

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._lazy_start()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._close_files()

    async def _run(self) -> None:
        assert self._q is not None
        while True:
            try:
                kind, payload = await self._q.get()
                self._write(kind, payload)
            except asyncio.CancelledError:
                self._close_files()
                raise
            except Exception as exc:  # noqa: BLE001
                self.stats["errors"] += 1
                if self.stats["errors"] in (1, 100, 1000):   # не залить лог
                    log.warning("recorder.write_failed", error=str(exc),
                                errors=self.stats["errors"])
                if self.stats["errors"] > 5000:
                    # Диск кончился или сломан: гасим запись, торговлю не трогаем.
                    log.error("recorder.disabled_after_errors", errors=self.stats["errors"])
                    self.enabled = False
                    self._close_files()
                    return

    # ---- файлы ----

    def _handle(self, kind: str) -> Any:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day != self._day:                 # сутки сменились — закрыть вчерашние
            self._close_files()
            self._day = day
        f = self._files.get(kind)
        if f is None:
            path = os.path.join(self.root, f"{kind}-{day}.jsonl.gz")
            f = gzip.open(path, "at", encoding="utf-8", compresslevel=6)
            self._files[kind] = f
        return f

    def _write(self, kind: str, payload: dict) -> None:
        f = self._handle(kind)
        f.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n")
        self.stats["written"] += 1
        self._since_flush += 1
        if self._since_flush >= _FLUSH_EVERY:
            f.flush()
            self._since_flush = 0

    def _close_files(self) -> None:
        for f in self._files.values():
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass
        self._files.clear()
