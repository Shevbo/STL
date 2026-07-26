"""Оракул торговой сессии MOEX FORTS — по ОФИЦИАЛЬНЫМ полям ISS.

Заказ оператора: сверяться с режимом/расписанием ММВБ из официального источника,
а не с зашитым календарём. Официальные машинные сигналы ISS (iss.moex.com):

  * marketdata.TRADE_SESSION_DATE — биржа САМА объявляет дату текущей/следующей
    сессии. В сб 19:23 после закрытия выходной сессии там уже стоит понедельник:
    «на сегодня всё, воскресенье торгов нет». Покрывает праздники, выходные
    сессии и их ранние закрытия БЕЗ какого-либо календаря в коде.
  * marketdata.TIME — время ПОСЛЕДНЕЙ СДЕЛКИ (биржевые часы). Замер по нему
    против SYSTIME (часы сервера ISS) даёт «торгуют ли прямо сейчас» на ОДНИХ
    часах ISS — локальные часы и их дрейф не участвуют.
  * engines/futures.json dailytable — официальный календарь исключений по датам
    (праздники, нерабочие выходные вроде 01–02.08.2026). Обновляется раз в
    сутки (первый запрос нового дня, «03:00-парсинг» без отдельного крона).

Урок v1 (2026-07-25): SYSTIME — часы СЕРВЕРА ISS, они тикают и после закрытия
сессии; сравнение «now vs SYSTIME» показывало «торги идут» в сб после 19:00.
Открытость определяет ТОЛЬКО связка TRADE_SESSION_DATE + свежесть TIME.

Фазы (phase):
  trading   — сессия сегодня, последняя сделка свежая: торги идут.
  break     — сессия сегодня, сделок нет несколько минут: клиринг/пауза/тишина.
  pre_open  — сессия объявлена на сегодня, сделок ещё не было.
  done      — TRADE_SESSION_DATE уже в будущем: на сегодня биржа закрыла торги.
  holiday   — сегодняшняя дата в dailytable с is_work_day=0.
  unknown   — ISS недоступен (open=None, потребитель трактует защитно).

`open` == (phase == 'trading'). Потребители (вотчер-probe, компаньон, SMS)
дополнительно получают phase/next_session для честных формулировок.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field

import httpx

_MSK = datetime.timezone(datetime.timedelta(hours=3))
_ISS_MD = "https://iss.moex.com/iss/engines/futures/markets/forts/securities/{code}.json"
_ISS_CAL = "https://iss.moex.com/iss/engines/futures.json"
# Свежесть последней сделки, при которой считаем «торги идут». 18 минут — чуть выше
# задержки публичного ISS (~15-16 мин): во время торгов lag=SYSTIME-TIME сам по себе ~16
# мин и проходит, а застой клиринга (~5 мин пауза + задержка ≈ 21 мин) и закрытие уже
# отсекаются. Порог 3 мин (было) считал живой рынок «закрытым». Главную точность даёт
# СДВИГ TIME между опросами (prev_trade_ms) — см. classify; порог — фолбэк на первый опрос.
_TRADE_FRESH_SEC = 1080
# Инструменты по умолчанию (фид агента их заменяет — коды следуют роллу сами).
_FALLBACK_CODES = ("RIU6", "SiU6")


@dataclass
class SessionState:
    open: bool | None            # True/False; None = ISS недоступен
    phase: str                   # trading|break|pre_open|done|holiday|unknown
    last_trade_ms: int           # эпоха-ms последней сделки (0 = неизвестно)
    iss_lag_sec: int             # SYSTIME - last_trade (сек): «тишина» по данным биржи
    session_date: str            # TRADE_SESSION_DATE как отдала биржа ('' = н/д)
    next_session: str            # следующая сессия, если сегодня торгов больше нет
    source_code: str
    checked_ms: int
    calendar: dict = field(default_factory=dict)   # {date: {'work': bool}} на сегодня/завтра
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "open": self.open, "phase": self.phase,
            "last_trade_ms": self.last_trade_ms, "iss_lag_sec": self.iss_lag_sec,
            "session_date": self.session_date, "next_session": self.next_session,
            "source_code": self.source_code, "checked_ms": self.checked_ms,
            "calendar": self.calendar, "error": self.error,
            # Совместимость с v1-потребителями (probe читает .open; лаг был в
            # lag_sec) — не ломаем то, что уже задеплоено на хостере.
            "lag_sec": self.iss_lag_sec, "systime_ms": self.last_trade_ms,
        }


def _parse_dt_ms(day: str, hms: str) -> int:
    """'2026-07-25' + '18:59:55' (МСК) -> epoch-ms; 0 при мусоре."""
    try:
        dt = datetime.datetime.strptime(f"{day} {hms}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=_MSK)
        return int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        return 0


def classify(*, today: str, session_date: str, systime_ms: int, trade_ms: int,
             holiday: bool = False, prev_trade_ms: int = 0,
             fresh_sec: int = _TRADE_FRESH_SEC) -> tuple[bool | None, str, int]:
    """Чистая логика: (open, phase, iss_lag_sec). Все времена — часы ISS.

    ОТКРЫТОСТЬ = ДВИЖЕНИЕ/свежесть ПОСЛЕДНЕЙ СДЕЛКИ, а НЕ TRADE_SESSION_DATE.
    Урок 2026-07-26 (дорогая ошибка): TRADE_SESSION_DATE — дата КЛИРИНГА (T+1). Во
    время активных торгов она ВСЕГДА завтрашняя (сегодня 26-е торгуют на миллиарды, а
    поле = 27-е). Прошлая логика `session_date > today → закрыто` поэтому говорила
    «биржа закрыта, курите до понедельника» ПРЯМО ВО ВРЕМЯ ТОРГОВ. Больше это поле
    открытость НЕ решает — только справочно (next_session).

    Публичный ISS отдаёт данные с задержкой ~15 мин, поэтому:
      1) если TIME сдвинулся с прошлого опроса (prev_trade_ms) — сделки ИДУТ, открыто;
      2) иначе свежесть: lag = SYSTIME-TIME <= fresh_sec (порог > задержки ISS) — открыто;
      3) иначе TIME замер: короткая пауза = break (клиринг/тонкий рынок), долгая = done.
    session_date НЕ участвует в решении.
    """
    if systime_ms <= 0:
        return None, "unknown", 0
    lag = max(0, (systime_ms - trade_ms) // 1000) if trade_ms > 0 else 10**9
    if holiday:
        return False, "holiday", lag
    if trade_ms <= 0:
        return False, "pre_open", lag
    if prev_trade_ms and trade_ms > prev_trade_ms:
        return True, "trading", lag          # TIME продвинулся между опросами → торги идут
    if lag <= fresh_sec:
        return True, "trading", lag          # свежая сделка в пределах задержки ISS
    return (False, "break", lag) if lag < 1800 else (False, "done", lag)


def codes_from_feed(feed: list[dict] | None) -> list[str]:
    codes = [str(f.get("code")) for f in (feed or []) if f.get("code")]
    return codes or list(_FALLBACK_CODES)


async def fetch_calendar(client: httpx.AsyncClient) -> dict:
    """Официальный календарь исключений (dailytable) на сегодня и завтра.
    {date: {'work': bool}}. Пусто — исключений нет (обычный день)."""
    r = await client.get(_ISS_CAL, params={"iss.meta": "off", "iss.only": "dailytable"})
    r.raise_for_status()
    dt = r.json().get("dailytable", {})
    cols, rows = dt.get("columns", []), dt.get("data", [])
    if "date" not in cols:
        return {}
    idx_d, idx_w = cols.index("date"), cols.index("is_work_day")
    today = datetime.datetime.now(tz=_MSK).date()
    want = {today.isoformat(), (today + datetime.timedelta(days=1)).isoformat()}
    out = {}
    for r_ in rows:
        d = str(r_[idx_d])
        if d in want:
            out[d] = {"work": bool(r_[idx_w])}
    return out


async def probe(codes: list[str], now_ms: int, *, client: httpx.AsyncClient | None = None,
                calendar: dict | None = None, prev_trade_ms: int = 0) -> SessionState:
    """Опрос ISS по нескольким кодам; берём инструмент с САМОЙ СВЕЖЕЙ сделкой
    (торгуется хоть один — рынок открыт). Полный провал -> open=None."""
    own = client is None
    cl = client or httpx.AsyncClient(timeout=8.0)
    best = {"trade_ms": 0, "systime_ms": 0, "session_date": "", "code": ""}
    errs: list[str] = []
    try:
        for code in codes[:4]:
            try:
                r = await cl.get(_ISS_MD.format(code=code),
                                 params={"iss.meta": "off", "iss.only": "marketdata"})
                r.raise_for_status()
                md = r.json().get("marketdata", {})
                cols, rows = md.get("columns", []), md.get("data", [])
                if not rows or "SYSTIME" not in cols:
                    continue
                m = dict(zip(cols, rows[0]))
                sys_raw = str(m.get("SYSTIME") or "")           # 'YYYY-MM-DD HH:MM:SS'
                sys_day, _, sys_hms = sys_raw.partition(" ")
                systime_ms = _parse_dt_ms(sys_day, sys_hms)
                # TIME — часы последней сделки ТЕКУЩЕГО торгового дня (биржевые).
                # День берём из SYSTIME: обе метки живут на часах ISS.
                trade_ms = _parse_dt_ms(sys_day, str(m.get("TIME") or "")) if m.get("TIME") else 0
                if trade_ms > systime_ms and trade_ms - systime_ms > 3_600_000:
                    trade_ms -= 86_400_000    # сделка «позже» сервера на часы = вчерашняя
                if trade_ms >= best["trade_ms"]:
                    best = {"trade_ms": trade_ms, "systime_ms": systime_ms,
                            "session_date": str(m.get("TRADE_SESSION_DATE") or ""),
                            "code": code}
            except Exception as exc:  # noqa: BLE001 — терпим по одному коду
                errs.append(f"{code}: {exc.__class__.__name__}")
    finally:
        if own:
            await cl.aclose()

    today = datetime.datetime.now(tz=_MSK).date().isoformat()
    cal = calendar or {}
    holiday = not cal.get(today, {}).get("work", True) if today in cal else False
    is_open, phase, lag = classify(
        today=today, session_date=best["session_date"],
        systime_ms=best["systime_ms"], trade_ms=best["trade_ms"], holiday=holiday,
        prev_trade_ms=prev_trade_ms)
    nxt = best["session_date"] if best["session_date"] and best["session_date"] > today else ""
    err = "" if best["systime_ms"] else ("; ".join(errs) or "ISS не ответил")
    return SessionState(
        open=is_open, phase=phase, last_trade_ms=best["trade_ms"],
        iss_lag_sec=int(lag if lag < 10**9 else 0),
        session_date=best["session_date"], next_session=nxt,
        source_code=best["code"], checked_ms=now_ms, calendar=cal, error=err)
