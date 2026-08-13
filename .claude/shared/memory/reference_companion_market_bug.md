---
name: companion-market-bug
description: next_session ISO-datetime parsed as bare date garbled text; m.html (mobile companion) silently drifts out of parity with companion.html (desktop)
metadata: 
  node_type: memory
  type: reference
  originSessionId: 1fdae9ae-bd50-4f7c-a988-0765fd5acc9d
  modified: 2026-08-01T06:59:06.406Z
---

`market.next_session` from `trader/market_session.py` (`SessionState.as_dict`) is a
FULL ISO datetime with time+offset (`datetime.isoformat()`), not a bare
`YYYY-MM-DD`. Both `frontend/public/companion.html` and `m.html` used to parse it
as `next_session.slice(5).split('-').reverse().join('.')` — correct only for a
bare date, garbage on real ISO input (`'03T07:00:00+03:00.08'`), and combined with
`white-space:nowrap` + `overflow:hidden` on the market row it silently clipped
into unreadable text on the panel ("сессия завершена · след. 03Т07:"). Fixed
2026-08-01 via `new Date(iso)` + `toLocaleDateString/toLocaleTimeString`
(`fmtNextSession` in both files). The 'done' phase message is now "Биржа
закрыта. Открытие торгов ДД.ММ ЧЧ:ММ" (was "сессия завершена").

**m.html (mobile companion) is a hand-maintained near-duplicate of
companion.html (desktop tray) and drifts silently** — it had no "заявки
ручные" section at all, no real/paper mode tag, no exit-only tag, no
ann%/chg%, and (more seriously) summed the robot total as bare `real_net`
instead of `real_total` (fix+VM), violating [[feedback_finres_with_open_position]].
Brought to parity 2026-08-01. When editing one of companion.html/m.html for a
snapshot-field change, check the other — there is no shared module, `trader/api/quik_companion.py`
`snapshot()` is the single source of truth for what fields exist.

**Deeper backend bug found the same day, same investigation (`trader/market_session.py
classify()`):** `now_ms` passed to `classify()` is exchange SYSTIME (from the last-polled
instrument's ISS marketdata), NOT wall-clock — and SYSTIME does NOT tick when the market
is inactive; it freezes at the last clearing moment (01.08.2026: RIU6/SiU6/GZU6 all froze
in lockstep at "2026-07-31 23:50:05" and were still byte-identical a minute after the
nominal weekend-session start 09:50, confirmed by live re-poll). `classify()` derived
"day" (for the dailytable holiday lookup + "today's sessions" filter) from this same
frozen `now_ms`, so on a long-closed day the holiday check silently compared YESTERDAY's
date — the `holiday` branch was effectively unreachable whenever SYSTIME lagged behind a
real date boundary, silently falling through to the generic `done` branch instead (same
`open=False` by luck here, since session_schedule also had no window that day — but
`next_open_ms` would be wrong in general). Fixed by threading `wall_ms` (the real poller
clock, already collected as `checked_ms` but unused) through to `classify()`, used ONLY
for day derivation — window-membership (`f_ms <= now_ms <= t_ms`) still gated on SYSTIME,
preserving the original "immune to VDS clock drift" property. A day-1 regression: making
`holiday` reachable exposed it was hardcoded to `next_open_ms=0` (never exercised before)
— panel's "next open" date used to come from the `done` branch it always fell into; fixed
to compute the same "earliest future session" as `done`. Both shipped + backend restarted
2026-08-01 ~09:52 MSK. Tests: `test_holiday_check_uses_wall_clock_not_frozen_systime` in
`tests/test_market_session.py` reproduces the exact scenario.

**Ground-truth check performed live 2026-08-01 (relates to [[reference_forts_weekend_trading]]):**
MOEX's own 2026 weekend-trading press release (moex.com/n95564) names explicit EXCLUSION
dates when the "сессия выходного дня" (09:50-19:00 MSK) does NOT run — 2026 list: 3-4 & 10-11
Jan, 14-15 Feb, 7-8 & 21-22 Mar, 9-10 May, 20-21 Jun, **1-2 & 15-16 Aug**, 12-13 Sep, 24-25
Oct, 5-6 Dec. Confirmed empirically for 01-02.08.2026: ISS `session_schedule` has no window
that weekend (only Mon 03.08's full day), zero of 476 FORTS instruments show today's
TRADEDATE, dailytable flags both dates `is_work_day=0` (a normal trading Saturday, e.g.
18.07, has NO dailytable row at all — the row's mere presence is itself an exclusion
signal), and a live re-poll one minute after the nominal 09:50 start showed zero change.
`classify()` reading `session_schedule`/`dailytable` directly (not a day-of-week guess) is
therefore ALREADY exclusion-aware by construction — no separate exclusion-list table
needed in code, ISS already encodes it.
