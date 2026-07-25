"""Оракул торговой сессии MOEX FORTS: открыта биржа сейчас или нет.

Зачем: система не должна ЗАШИВАТЬ расписание (будни/выходные/праздники меняются,
а календарь в коде — источник галлюцинаций). Вместо этого читаем ФАКТ с MOEX ISS.

Ground truth — поле SYSTIME в marketdata FORTS: биржевое системное время. Пока
идут торги, SYSTIME догоняет стену. Как только сессия закрылась, SYSTIME
замирает на времени последней сделки. Значит:

    рынок открыт  <=>  |сейчас(МСК) - SYSTIME| <= допуск

Это одинаково работает для будней, выходных (торги выходного дня по ограниченному
списку) и праздников — потому что описывает не календарь, а реальную сессию.
Внутридневные клиринги (14:00-14:05, 18:45-19:00) SYSTIME тоже замораживают —
и это ПРАВИЛЬНО читается как «сейчас не торгуют»: сделок в клиринг нет, и глушить
тревогу по замершей ленте в это время — верно, а не баг.

Зачем это критично: вотчер видел замершую ленту агента и паузил РЕАЛЬНЫХ роботов,
считая это «реплеем после рестарта QUIK». Но когда биржа просто закрыта (ночь,
пауза перед выходной сессией), замершая лента — НОРМА, а не авария. Различить два
случая можно только зная, торгует ли биржа на самом деле:

  * ISS говорит ЗАКРЫТО + лента агента замерла  -> ожидаемо, тревоги нет.
  * ISS говорит ОТКРЫТО + лента агента замерла   -> НАСТОЯЩАЯ авария (окно
    «Таблица всех сделок» в QUIK закрылось / фид умер) — тревога законна.

Никакого ввода в реал этот модуль не делает: только читает ISS и отдаёт факт.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

import httpx

_MSK = datetime.timezone(datetime.timedelta(hours=3))
_ISS = "https://iss.moex.com/iss/engines/futures/markets/forts/securities/{code}.json"
# Допуск: ISS сам кэширует ~30-60с, поэтому не 5с. 180с надёжно отличает живую
# сессию (SYSTIME тикает каждую секунду) от замершей (отстаёт на часы).
_OPEN_TOLERANCE_SEC = 180
# Инструменты по умолчанию, если у агента нет своего фида. Роллятся вместе с
# фронт-месяцем — поэтому основной путь берёт коды из фида агента (следуют роллу
# сами), а это лишь запасной вариант. Обновлять при ролле руками НЕ надо, если
# агент жив: см. codes_from_feed().
_FALLBACK_CODES = ("RIU6", "SiU6")


@dataclass
class SessionState:
    open: bool | None          # True/False, None = не удалось определить
    systime_ms: int            # биржевое SYSTIME в epoch-ms МСК (0 если неизвестно)
    lag_sec: int               # сейчас - SYSTIME, сек (>= 0); большой = закрыто
    source_code: str           # по какому инструменту определили
    checked_ms: int            # когда проверяли (epoch-ms)
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "open": self.open, "systime_ms": self.systime_ms,
            "lag_sec": self.lag_sec, "source_code": self.source_code,
            "checked_ms": self.checked_ms, "error": self.error,
        }


def _parse_systime_ms(s: str) -> int:
    """'2026-07-24 23:50:15' (МСК) -> epoch-ms. 0 при мусоре."""
    try:
        dt = datetime.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_MSK)
        return int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        return 0


def classify(systime_ms: int, now_ms: int, tol_sec: int = _OPEN_TOLERANCE_SEC) -> tuple[bool | None, int]:
    """Чистая логика: (открыт?, лаг_сек). None если SYSTIME неизвестен.

    Лаг считаем по модулю: если часы биржи ушли ВПЕРЁД (бывает при дрейфе),
    сессию всё равно считаем открытой, а не роняем в «закрыто» из-за знака.
    """
    if systime_ms <= 0:
        return None, 0
    lag = abs(now_ms - systime_ms) // 1000
    return lag <= tol_sec, int(lag)


def codes_from_feed(feed: list[dict] | None) -> list[str]:
    """Коды из фида агента (health.feed) — следуют роллу сами. Пусто -> запасные."""
    codes = [str(f.get("code")) for f in (feed or []) if f.get("code")]
    return codes or list(_FALLBACK_CODES)


async def probe(codes: list[str], now_ms: int, *, client: httpx.AsyncClient | None = None,
                tol_sec: int = _OPEN_TOLERANCE_SEC) -> SessionState:
    """Спросить ISS по нескольким инструментам и взять САМЫЙ СВЕЖИЙ SYSTIME: если
    хоть один торгуется — рынок открыт. Одна сетевая ошибка не должна давать
    «закрыто» (это разблокировало бы тревогу), поэтому при полном провале
    возвращаем open=None (неизвестно) — потребитель трактует как «защитно».
    """
    own = client is None
    cl = client or httpx.AsyncClient(timeout=8.0)
    best_ms, best_code, errs = 0, "", []
    try:
        for code in codes[:4]:  # хватает пары ликвидных; не молотим весь рынок
            try:
                r = await cl.get(_ISS.format(code=code),
                                 params={"iss.meta": "off", "iss.only": "marketdata"})
                r.raise_for_status()
                md = r.json().get("marketdata", {})
                cols, rows = md.get("columns", []), md.get("data", [])
                if "SYSTIME" not in cols or not rows:
                    continue
                idx = cols.index("SYSTIME")
                ms = _parse_systime_ms(rows[0][idx] or "")
                if ms > best_ms:
                    best_ms, best_code = ms, code
            except Exception as exc:  # noqa: BLE001 — по одному коду терпим
                errs.append(f"{code}: {exc.__class__.__name__}")
    finally:
        if own:
            await cl.aclose()

    is_open, lag = classify(best_ms, now_ms, tol_sec)
    err = "" if best_ms else ("; ".join(errs) or "ISS не ответил")
    return SessionState(open=is_open, systime_ms=best_ms, lag_sec=lag,
                        source_code=best_code, checked_ms=now_ms, error=err)
