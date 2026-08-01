"""Оракул торговой сессии MOEX FORTS — по ОФИЦИАЛЬНОМУ машиночитаемому расписанию ISS.

Единый источник правды (заказ оператора): расписание торгов MOEX — moex.com/s1167.
Его МАШИНОЧИТАЕМАЯ подложка — ISS `engines/futures.json`, блок `session_schedule`:
точные окна сессий по датам (утренняя/основная/вечерняя/доп. сессия ВЫХОДНОГО ДНЯ,
плюс аукцион открытия, клиринг, расчётная). Это те же данные, что на странице биржи.

ЖЕЛЕЗОБЕТОННЫЙ АЛГОРИТМ (никаких эвристик по дню недели или самодельных полей):
  1. «Сейчас» = SYSTIME из ISS marketdata (часы БИРЖИ, не локальные — иммунитет к дрейфу
     часов хостера/VDS). SYSTIME — реальное серверное время (в отличие от TIME/последней
     сделки, которая на публичном ISS задержана ~15 мин).
  2. Открыт ли рынок = попадает ли SYSTIME в ТОРГОВОЕ окно сегодняшней даты из
     session_schedule (типы morning/main/evening/weekend). Клиринг/расчёт/аукцион — не торги.
  3. Праздник/сокращённый день — из dailytable (is_work_day=0). Но session_schedule и так
     не даёт торговых окон в нерабочий день, dailytable — ремень+подтяжки.
  4. Свежесть последней сделки (SYSTIME-TIME) — ВТОРИЧНый сигнал здоровья фида (лаг ленты),
     а НЕ решение open/closed. Расписание авторитетно.

Дорогие ошибки, которые это закрывает:
  * 2026-07-25: эмпирика «now vs SYSTIME» (SYSTIME тикает после закрытия) → «торги идут» в
    сб после 19:00.
  * 2026-07-26: `TRADE_SESSION_DATE > today → закрыто`. Но это дата КЛИРИНГА (T+1), во
    время торгов ВСЕГДА завтрашняя → «биржа закрыта, до понедельника» ПРЯМО ВО ВРЕМЯ
    торгов на миллиарды. Теперь TRADE_SESSION_DATE в решении НЕ участвует.

Фазы (phase): trading | break | pre_open | done | holiday | unknown.
`open` == (phase == 'trading'). Потребители (вотчер-probe, компаньон, SMS) дополнительно
получают phase/session_type/next_session для честных формулировок.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field

import httpx

_MSK = datetime.timezone(datetime.timedelta(hours=3))
_ISS_MD = "https://iss.moex.com/iss/engines/futures/markets/forts/securities/{code}.json"
_ISS_SCHED = "https://iss.moex.com/iss/engines/futures.json"
# Типы окон, в которых РЕАЛЬНО идут сделки. Клиринг/расчёт/аукцион открытия — НЕ торги.
_TRADING_TYPES = frozenset({"morning_session", "main_session", "evening_session", "weekend_session"})
# Инструменты по умолчанию для чтения SYSTIME (фид агента их заменяет — коды следуют роллу).
_FALLBACK_CODES = ("RIU6", "SiU6")


@dataclass
class SessionState:
    open: bool | None            # True/False; None = данных нет (защитная трактовка)
    phase: str                   # trading|break|pre_open|done|holiday|unknown
    session_type: str            # morning|main|evening|weekend|'' — какое окно сейчас/след.
    now_ms: int                  # часы БИРЖИ (SYSTIME), эпоха-ms
    next_open_ms: int            # старт следующего торгового окна (0 = неизвестно)
    last_trade_ms: int           # последняя сделка (для лага ленты)
    iss_lag_sec: int             # SYSTIME - last_trade: «тишина» по данным биржи (здоровье фида)
    source_code: str
    checked_ms: int
    error: str = ""
    calendar: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        nxt = _iso_ms(self.next_open_ms)
        return {
            "open": self.open, "phase": self.phase, "session_type": self.session_type,
            "now_ms": self.now_ms, "next_open_ms": self.next_open_ms,
            "next_session": nxt, "last_trade_ms": self.last_trade_ms,
            "iss_lag_sec": self.iss_lag_sec, "source_code": self.source_code,
            "checked_ms": self.checked_ms, "calendar": self.calendar, "error": self.error,
            # Совместимость с прежними потребителями (probe/вотчдог читают .open; лаг был в
            # lag_sec; часы биржи были в systime_ms) — не ломаем задеплоенное.
            "lag_sec": self.iss_lag_sec, "systime_ms": self.now_ms,
        }


def _parse_dt_ms(day: str, hms: str) -> int:
    """'2026-07-26' + '11:54:37' (МСК) -> epoch-ms; 0 при мусоре."""
    try:
        dt = datetime.datetime.strptime(f"{day} {hms}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=_MSK)
        return int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        return 0


def _parse_iss_dt(s: str) -> int:
    """'YYYY-MM-DD HH:MM:SS' (МСК) -> epoch-ms; 0 при пустом/мусоре."""
    day, _, hms = str(s or "").partition(" ")
    return _parse_dt_ms(day, hms)


def _iso_ms(ms: int) -> str:
    if ms <= 0:
        return ""
    return datetime.datetime.fromtimestamp(ms / 1000, tz=_MSK).isoformat()


def parse_schedule(session_rows: list, session_cols: list,
                   daily_rows: list, daily_cols: list) -> dict:
    """Разбирает ISS session_schedule + dailytable в структуру для classify.

    Возвращает {'sessions': [(from_ms, till_ms, type)], 'holidays': {date_str}}.
    В session_schedule ФАКТИЧЕСКАЯ дата окна лежит в time_from/time_till (а НЕ в
    trade_session_date — там дата клиринга T+1)."""
    sessions: list[tuple[int, int, str]] = []
    if session_cols:
        i_type = session_cols.index("type") if "type" in session_cols else -1
        i_from = session_cols.index("time_from") if "time_from" in session_cols else -1
        i_till = session_cols.index("time_till") if "time_till" in session_cols else -1
        for r in session_rows:
            typ = str(r[i_type]) if i_type >= 0 else ""
            if typ not in _TRADING_TYPES:
                continue
            f_ms = _parse_iss_dt(r[i_from]) if i_from >= 0 else 0
            t_ms = _parse_iss_dt(r[i_till]) if i_till >= 0 else 0
            if f_ms and t_ms:
                sessions.append((f_ms, t_ms, typ))
    sessions.sort()
    holidays: set[str] = set()
    if daily_cols and "date" in daily_cols and "is_work_day" in daily_cols:
        i_d, i_w = daily_cols.index("date"), daily_cols.index("is_work_day")
        for r in daily_rows:
            if not int(r[i_w] or 0):
                holidays.add(str(r[i_d]))
    return {"sessions": sessions, "holidays": holidays}


async def fetch_schedule(client: httpx.AsyncClient) -> dict:
    """Тянет ОФИЦИАЛЬНОЕ расписание FORTS из ISS (session_schedule + dailytable).
    Обновлять раз в ~час/сутки — оно почти статично, лишь дополняется новыми датами."""
    r = await client.get(_ISS_SCHED, params={"iss.meta": "off",
                                              "iss.only": "session_schedule,dailytable"})
    r.raise_for_status()
    d = r.json()
    ss = d.get("session_schedule", {})
    dt = d.get("dailytable", {})
    return parse_schedule(ss.get("data", []), ss.get("columns", []),
                          dt.get("data", []), dt.get("columns", []))


def _short_type(typ: str) -> str:
    return {"morning_session": "morning", "main_session": "main",
            "evening_session": "evening", "weekend_session": "weekend"}.get(typ, "")


def classify(*, now_ms: int, schedule: dict, wall_ms: int | None = None) -> tuple[bool | None, str, str, int]:
    """ЖЕЛЕЗОБЕТОН: (open, phase, session_type, next_open_ms) по официальным окнам.

    now_ms — часы БИРЖИ (SYSTIME). Решение об ОТКРЫТОСТИ (попадание в окно) —
    ТОЛЬКО по нему, иммунитет к дрейфу часов хостера/VDS. Но SYSTIME берётся из
    marketdata последней ОПРОШЕННОЙ сделки и НЕ тикает, пока биржа неактивна —
    он застывает на моменте последнего клиринга (01.08.2026: SYSTIME всех
    инструментов синхронно замер на «пятница 23:50:05», хотя на дворе уже
    суббота). Поэтому ДЕНЬ для праздничного чека и фильтра «сегодняшних» окон
    берём из wall_ms (реальные часы поллера) — иначе в затяжной паузе/выходной
    holiday-чек сверяет ВЧЕРАШНЮЮ дату и молча проваливается в общий 'done'
    (тот же итог open=False здесь СЛУЧАЙНО совпал, но при другом наборе окон
    это давало бы неверный next_open_ms). Никаких дней недели и самодельных
    полей — только session_schedule + dailytable.
    """
    sessions = (schedule or {}).get("sessions") or []
    holidays = (schedule or {}).get("holidays") or set()
    if now_ms <= 0 or not sessions:
        return None, "unknown", "", 0
    day = datetime.datetime.fromtimestamp((wall_ms or now_ms) / 1000, tz=_MSK).date().isoformat()
    if day in holidays:
        # next_open_ms — так же, как в ветке 'done' ниже: ближайшее окно в будущем.
        # Раньше здесь стоял хардкод 0 — с этим ветка 'holiday' была недостижима
        # (см. баг выше), и панель "открытие торгов ДД.ММ" ползла случайно из
        # ветки 'done'; теперь 'holiday' достижима и обязана нести ту же дату.
        nxt_all = [s[0] for s in sessions if s[0] > now_ms]
        return False, "holiday", "", (min(nxt_all) if nxt_all else 0)
    today = [s for s in sessions if _iso_ms(s[0]).startswith(day)]
    # В торговом окне прямо сейчас?
    for f_ms, t_ms, typ in today:
        if f_ms <= now_ms <= t_ms:
            return True, "trading", _short_type(typ), t_ms
    # Не в окне: до первого окна дня / между окнами / после последнего.
    future = [s for s in today if s[0] > now_ms]
    if future:
        nxt = min(s[0] for s in future)
        # если сегодня уже были окна раньше — это ПАУЗА между сессиями/клиринг, иначе утро
        had_earlier = any(s[1] < now_ms for s in today)
        return False, ("break" if had_earlier else "pre_open"), "", nxt
    # окон сегодня больше нет — следующее в будущих датах
    nxt_all = [s[0] for s in sessions if s[0] > now_ms]
    return False, "done", "", (min(nxt_all) if nxt_all else 0)


def codes_from_feed(feed: list[dict] | None) -> list[str]:
    codes = [str(f.get("code")) for f in (feed or []) if f.get("code")]
    return codes or list(_FALLBACK_CODES)


async def probe(codes: list[str], now_ms: int, *, client: httpx.AsyncClient | None = None,
                schedule: dict | None = None) -> SessionState:
    """Опрос: берём SYSTIME (часы биржи) и свежесть сделки из ISS marketdata по нескольким
    кодам, решение об открытости — по ОФИЦИАЛЬНОМУ расписанию `schedule` (см. fetch_schedule).
    Расписание не передали / ISS молчит -> open=None (потребитель трактует защитно)."""
    own = client is None
    cl = client or httpx.AsyncClient(timeout=8.0)
    best = {"systime_ms": 0, "trade_ms": 0, "code": ""}
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
                sys_raw = str(m.get("SYSTIME") or "")           # 'YYYY-MM-DD HH:MM:SS' (часы биржи)
                sys_day, _, sys_hms = sys_raw.partition(" ")
                systime_ms = _parse_dt_ms(sys_day, sys_hms)
                trade_ms = _parse_dt_ms(sys_day, str(m.get("TIME") or "")) if m.get("TIME") else 0
                if trade_ms > systime_ms and trade_ms - systime_ms > 3_600_000:
                    trade_ms -= 86_400_000    # сделка «позже» сервера на часы = вчерашняя
                # берём инструмент с самой свежей сделкой (SYSTIME у всех одинаков)
                if systime_ms and (not best["systime_ms"] or trade_ms >= best["trade_ms"]):
                    best = {"systime_ms": systime_ms, "trade_ms": trade_ms, "code": code}
            except Exception as exc:  # noqa: BLE001 — терпим по одному коду
                errs.append(f"{code}: {exc.__class__.__name__}")
    finally:
        if own:
            await cl.aclose()

    now_biz = best["systime_ms"]
    # now_ms (аргумент probe) — реальные часы поллера, не биржи; передаём как wall_ms,
    # чтобы день праздничного чека не застревал вместе с SYSTIME (см. classify()).
    is_open, phase, stype, next_ms = classify(now_ms=now_biz, schedule=schedule or {}, wall_ms=now_ms)
    lag = max(0, (now_biz - best["trade_ms"]) // 1000) if (now_biz and best["trade_ms"] > 0) else 0
    err = "" if now_biz else ("; ".join(errs) or "ISS не ответил")
    if not (schedule or {}).get("sessions"):
        err = (err + "; расписание не загружено").strip("; ")
    return SessionState(
        open=is_open, phase=phase, session_type=stype, now_ms=now_biz,
        next_open_ms=next_ms, last_trade_ms=best["trade_ms"], iss_lag_sec=int(lag),
        source_code=best["code"], checked_ms=now_ms,
        calendar={"holidays": sorted((schedule or {}).get("holidays") or set())[:10]}, error=err)
