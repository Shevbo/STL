"""Запись сырого рынка на диск: стакан, тики, жизненный цикл заявок, отказы.

ЗАЧЕМ. Бэктестер сегодня исполняет по close минутного бара, стакана у него нет
вовсе (BacktestRuntime.get_orderbook возвращает пустые списки), спред и
проскальзывание не моделируются. Из-за этого целый класс стратегий непроверяем, а
проверяемый систематически оптимистичен. Всё нужное агент УЖЕ шлёт в STL — просто
никто это не сохранял, и каждый день данные умирали в памяти.

ИЗОЛЯЦИЯ (главное требование). Сбор не имеет права сломать торговлю:
  • в торговом пути ровно один вызов record_frame(), он никогда не бросает;
  • В ПРИЁМНОМ ЦИКЛЕ НЕТ КОНВЕРТАЦИИ: в очередь кладётся ссылка на protobuf, а
    MessageToDict и гейт делает писательский таск. На ленте (батчи по сотни
    сделок) синхронная конвертация украла бы миллисекунды у цикла, по которому
    идут order_update и trans_reply реальной торговли;
  • очередь ОГРАНИЧЕНА: при переполнении кадры молча теряются, а не копятся;
  • любая ошибка файловой системы гасит запись до конца дня, торговля продолжается;
  • по умолчанию ВЫКЛЮЧЕНО, включается QUIK_RECORD_DIR.

ФОРМАТ. Простой JSONL, файл на (дату, тип); сжимается ОДИН раз при смене суток.
Первая версия писала сразу в gzip в режиме дозаписи, и файлы оказались нечитаемы:
поток не завершён пока файл открыт, zcat падал на середине («invalid compressed
data»). Архив, который нельзя прочитать до конца дня и который рвётся при падении
процесса, архивом не является. Не бинарь и не БД намеренно: pandas читает
напрямую, а таблица на миллионы строк в сутки уже роняла снапшот компаньона.

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
import time
from datetime import datetime, timezone
from typing import Any

import structlog
from google.protobuf.json_format import MessageToDict

log = structlog.get_logger()


def _to_dict(msg) -> dict | None:
    """protobuf -> dict. Зовётся ТОЛЬКО из писательского таска, не из приёмного цикла."""
    try:
        return MessageToDict(msg, preserving_proto_field_name=True)
    except Exception:  # noqa: BLE001
        return None

# Очередь ограничена: сбор данных НИКОГДА не должен создавать давление на приём
# кадров. Полная очередь = теряем кадры и считаем их в dropped, а не тормозим
# торговый путь.
_QUEUE_MAX = 20_000
_FLUSH_EVERY = 200          # строк между flush: компромисс потери и сисколлов


class MarketRecorder:
    """Пишет кадры агента на диск. Все методы безопасны в торговом пути."""

    def __init__(self, root: str | None = None, context=None) -> None:
        self.root = root or os.environ.get("QUIK_RECORD_DIR") or ""
        self.enabled = bool(self.root)
        # Опциональный callable () -> dict: обстановка на момент кадра (свежесть
        # фида, фаза сессии). Зовётся в ПИСАТЕЛЬСКОМ таске, не в приёме. Если
        # бросит — кадр всё равно пишется, просто без контекста.
        self.context = context
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
        """Единственная точка входа из торгового пути.

        Здесь НЕ конвертируем: в очередь кладётся ссылка на protobuf, а
        MessageToDict и гейт по содержимому делает писательский таск. Разница
        станет решающей на ленте: она идёт батчами по 300 мс, в активной сессии
        по RI это сотни сделок в батче, и синхронная конвертация украла бы
        миллисекунды у ПРИЁМНОГО цикла — того самого, по которому приходят
        order_update и trans_reply реальной торговли (замечание real-trade
        12.08.2026). Задержка подтверждения заявки это цена исполнения.
        """
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
            # Метка ставится ЗДЕСЬ, в момент приёма, а не в писателе: между
            # очередью и записью проходит неизвестное время, и упорядочить кадры
            # разных типов по времени записи нельзя. Часы STL, а не агента:
            # часы VDS уже уезжали на минуты, а QLua трункирует большие целые
            # (см. гоняющийся w32tm resync и правило мерить RTT по своим часам).
            # Без этого поля заявку невозможно склеить со стаканом на момент
            # отправки, а в этом весь смысл архива (пункт «а» real-trade).
            self._q.put_nowait((kind, getattr(msg, field), time.time()))
        except asyncio.QueueFull:
            self.stats["dropped"] += 1
        except Exception:  # noqa: BLE001 — кадр архива не стоит сбоя приёма
            self.stats["errors"] += 1

    def record(self, kind: str, payload: dict) -> None:
        """Положить готовый dict в очередь (используется тестами и вызовами,
        у которых protobuf нет). НИКОГДА не бросает и не блокирует."""
        if not self.enabled or self._q is None:
            return
        try:
            self._q.put_nowait((kind, payload, time.time()))
        except asyncio.QueueFull:
            self.stats["dropped"] += 1
        except Exception:  # noqa: BLE001 — запись данных не стоит ни одного сбоя торговли
            self.stats["errors"] += 1

    def _same_as_last(self, kind: str, payload: dict) -> bool:
        """Гейт по СОДЕРЖИМОМУ для стакана и тиков: писать только изменения."""
        if kind not in ("book", "tick"):
            return False
        try:
            key = f"{kind}:{payload.get('code', '')}"
            sig = self._signature(kind, payload)
            if sig is None:
                return False
            if self._last.get(key) == sig:
                return True
            self._last[key] = sig
            return False
        except Exception:  # noqa: BLE001
            return False

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
                kind, item, recv = await self._q.get()
                # Конвертация и гейт живут ЗДЕСЬ, а не в приёмном цикле: кадр,
                # который гейт выбросит, не должен стоить ничего.
                payload = item if isinstance(item, dict) else _to_dict(item)
                if payload is None:
                    self.stats["errors"] += 1
                    continue
                if self._same_as_last(kind, payload):
                    self.stats["skipped_same"] += 1
                    continue
                payload["stl_recv_ms"] = int(recv * 1000)
                if self.context is not None:
                    try:
                        payload.update(self.context())
                    except Exception:  # noqa: BLE001 — контекста нет, кадр пишем
                        pass
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
        if day != self._day:                 # сутки сменились — закрыть и сжать
            self._rotate()
            self._day = day
        f = self._files.get(kind)
        if f is None:
            # ПРОСТОЙ текст, не gzip. Первая версия писала gzip в режиме дозаписи, и
            # файлы оказались нечитаемы: поток не завершён, пока файл открыт, и zcat
            # падал на середине («invalid compressed data»). Архив, который нельзя
            # прочитать до конца дня и который рвётся при падении процесса, не архив.
            # Сжимаем ОДИН раз при ротации суток — там файл уже закрыт и целостен.
            path = os.path.join(self.root, f"{kind}-{day}.jsonl")
            f = open(path, "a", encoding="utf-8")  # noqa: SIM115 — живёт до ротации
            self._files[kind] = f
        return f

    def _rotate(self) -> None:
        """Закрыть вчерашние файлы и сжать их. Сжатие закрытого файла целостно."""
        paths = [getattr(f, "name", None) for f in self._files.values()]
        self._close_files()
        for path in paths:
            if not path or not os.path.exists(path) or path.endswith(".gz"):
                continue
            try:
                with open(path, "rb") as src, gzip.open(path + ".gz", "wb", 6) as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
                os.remove(path)
            except Exception as exc:  # noqa: BLE001 — не сжалось, останется как есть
                log.warning("recorder.compress_failed", path=path, error=str(exc))

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
