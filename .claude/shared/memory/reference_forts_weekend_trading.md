---
name: reference-forts-weekend-trading
description: FORTS trades most weekends via ISS session_schedule (never guess from day-of-week) — but MOEX publishes yearly exclusion dates (e.g. 2026-08-01/02) when weekend trading is genuinely off
metadata: 
  node_type: memory
  type: reference
  originSessionId: 54d0cf23-21ef-4926-81c5-db49d8bc51ce
  modified: 2026-08-01T06:30:48.209Z
---

FORTS (MOEX срочный рынок) ТОРГУЕТ ПО ВЫХОДНЫМ. Расписание давно включает субботу и воскресенье.

**Why this matters:** I (Claude) hallucinated "31.05.2026 воскресенье — FORTS закрыт" THREE times,
wrongly concluding no live ticks would generate until Monday. This is FALSE. The user corrected it
explicitly. FORTS has weekend trading sessions.

**How to apply:**
- NEVER assume the market is closed based on day-of-week. Do not say "выходной → рынка нет".
- NEVER downgrade a trading-health problem to "harmless, it's the weekend". The trading day
  opens on Sat/Sun too. A stuck tape / stale bars / paused real robots on a Saturday is JUST AS
  URGENT as on a weekday — the session opens 07:00 MSK regardless. The user corrected me AGAIN
  (2026-07-25) for framing a ~8h stuck-tape + auto-paused real robots as "безвредно, выходной".
- If checking whether live data is flowing, VERIFY empirically (fetch latest ISS bar timestamp,
  check quote feed) — do not infer from the calendar.
- A deployed live/paper robot CAN generate trades on Sat/Sun. Expect live ticks any day.

**2026-07-26 — ДОРОГАЯ ОШИБКА, тот же класс, другой механизм.** Я построил "официальный
оракул" на `TRADE_SESSION_DATE > today → закрыто` и сказал оператору "выходной, биржа
закрыта, до понедельника" — ПРЯМО ВО ВРЕМЯ активных торгов на миллиарды. Причина:
**`marketdata.TRADE_SESSION_DATE` — дата КЛИРИНГА (T+1), во время торгов ВСЕГДА
завтрашняя.** Это НЕ "сегодня торгов нет". Использовать её как open/closed — ВСЕГДА даёт
"закрыто". Хуже: вотчдог-probe гейтит авто-паузу реальных роботов через `open` этого
оракула → ложное "закрыто" ОТКЛЮЧАЛО защиту (лаг-тейпа, расхождение баров) во время
торгов.

**Правильный сигнал открытости (trader/market_session.py classify):** СДВИГ `TIME`
последней сделки между опросами (`prev_trade_ms`) — точный, не зависит от задержки; ИЛИ
свежесть `lag=SYSTIME-TIME < 18 мин` (публичный ISS отдаёт данные с задержкой ~16 мин,
порог 3 мин считал живой рынок закрытым). `TRADE_SESSION_DATE` — только справочно
(next_session). Закрытие ловится по ЗАСТЫВШЕЙ сделке (TIME замер + lag растёт), НЕ по дате.
Проверять живьём: `VALTODAY` (миллиарды = торгуют), свежесть `TIME` по НЕСКОЛЬКИМ
инструментам. См. [[reference_equity_curve_lies]] — тот же принцип «доверяй факту, не полю».

**2026-08-01 — уточнение (НЕ отмена правила).** «Торгует по выходным» — это правило
«не угадывай по дню недели», а не «выходные = гарантированно торги». MOEX публикует на
год вперёд СПИСОК ИСКЛЮЧЕНИЙ — суббот/воскресений, когда сессии выходного дня НЕ
проводятся (пресс-релиз moex.com/n95564; на 2026: 3-4 и 10-11 янв, 14-15 фев, 7-8 и
21-22 мар, 9-10 мая, 20-21 июн, **1-2 и 15-16 авг**, 12-13 сен, 24-25 окт, 5-6 дек).
Обычная сессия выходного дня — 09:50-19:00 МСК. Проверено эмпирически 01.08.2026
(исключённая суббота): ISS `session_schedule` не даёт окна на 01-02.08 (только на
03.08 понедельник), ни один из 476 инструментов FORTS не торговал сегодня
(TRADEDATE≠today), дневные свечи RIU6/SiU6 не содержат прошлую субботу/воскресенье
(25-26.07) — та же картина. `trader/market_session.py: classify()` УЖЕ корректен: он
не проверяет день недели, а читает `session_schedule` из ISS напрямую — если биржа не
опубликовала окно на дату, `classify()` честно вернёт `done`/`pre_open`, и это НЕ баг.
**Как применять:** не отменять "не угадывай по дню недели" — по-прежнему проверять
`session_schedule`/факт сделок, а не календарь в уме. Но и не паниковать, увидев
«закрыто» в выходной — сначала свериться со списком исключений MOEX (или довериться
`classify()`, он уже это делает) прежде чем считать «закрыто по выходным» багом.

Related: [[project-lab-mvp-state]] (live trading / paper mode).
