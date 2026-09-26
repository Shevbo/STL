#!/usr/bin/env bash
# STL trading-health probe. Run on the hoster. Prints ONE Russian problem line per
# detected issue, prefixed "key|" for the smain watchdog's per-key cooldown; prints
# NOTHING when trading is healthy. Consumed by smain ~/bin/stl-watchdog.sh.
# Born 2026-07-21: the daily_order_cap silently froze every robot exit for 2.5h and
# the twice-a-day launch SMS could not see it — this probe watches CONTINUOUSLY.
# 2026-07-22: structured activation log (~/stl-watchdog-runs.jsonl) for the STL
# watchdog-log page + operator config (~/stl-watchdog-config.json, editable from
# that page): per-event escalation toggles, auto-pause switches, thresholds.
set -uo pipefail
cd ~/apps/shectory-trader || { echo "api_down|STL: каталог приложения отсутствует на хостере."; exit 0; }
set -a; . ~/.shectory_trade.env; set +a
PY=$(/home/ubuntu/.local/bin/poetry env info --path)/bin/python
TK=$("$PY" -c "import os;from trader.auth.portal import make_session_token as m;print(m('bshevelev75@gmail.com',os.environ['SHECTORY_AUTH_BRIDGE_SECRET']))" 2>/dev/null) \
  || { echo "api_down|STL: сервис не отвечает (не выпустился токен)."; exit 0; }
export TK
"$PY" - <<'PYEOF'
import json, os, time, urllib.request

# Operator config (edited from the watchdog-log page via STL API). Missing file
# or field -> these defaults, which reproduce the pre-config behaviour exactly.
CFG_DEFAULTS = {
    "escalate": {"api_down": True, "link_down": True, "runner_sick": True,
                 "cap_near": True, "cap_full": True, "tape_lag": True,
                 "bars": True, "hb": True, "ord": True,
                 "paused": True, "pausefail": True, "backtest_stuck": True,
                 "vds_mem": True, "vds_mem_crit": True, "quik_state": True},
    "autopause_tape_lag": True,
    "autopause_bars": True,
    "thresholds": {"tape_lag_sec": 120, "cap_warn_pct": 85, "hb_sec": 150,
                   "order_recheck_sec": 15, "bars_atr_mult": 3.0, "bars_pct": 0.5,
                   "backtest_stuck_sec": 3600},
}
cfg = dict(CFG_DEFAULTS)
try:
    with open(os.path.expanduser("~/stl-watchdog-config.json"), encoding="utf-8") as f:
        user = json.load(f)
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k] = {**cfg[k], **v}
        else:
            cfg[k] = v
except Exception:
    pass
THR = cfg["thresholds"]

def esc_on(key):
    """Escalation toggle by problem-key CATEGORY (bars_x/hb_x/ord_x -> family)."""
    if key.startswith("barsfresh") or key.startswith("tapelagquiet"):
        return False  # informational (quiet market / fresh tape): log-only, never SMS
    fam = key.split("_")[0]
    if fam not in ("bars", "hb", "ord", "paused", "pausefail"):
        fam = key
    if key.startswith("pausefail"):
        fam = "pausefail"
    elif key.startswith("paused"):
        fam = "paused"
    return bool(cfg["escalate"].get(fam, True))

def get(path):
    req = urllib.request.Request("http://localhost:8000" + path,
                                 headers={"Authorization": "Bearer " + os.environ["TK"]})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.load(r)

problems = []
try:
    als = get("/api/v1/quik/agent-local-status")
    mir = get("/api/v1/quik/robots-mirror")
except Exception as e:
    print(f"api_down|STL: API не отвечает ({type(e).__name__}).")
    raise SystemExit(0)

now = int(time.time() * 1000)
checked = ["API STL", "линк агента (зеркало)", "runner", "дневной лимит ордеров"]
recv = mir.get("received_at_ms") or 0
if not recv or (now - recv) / 1000 > 180:
    problems.append(("link_down", "агент НЕ на связи: зеркало старше 3 мин."))

agent = als.get("agent") or {}
if recv and not agent.get("link_up", True):
    problems.append(("link_down", "агент сообщает обрыв линка."))

h = als.get("health") or {}
if not h.get("runner_healthy", True):
    problems.append(("runner_sick", "runner НЕЗДОРОВ: роботы не исполняются."))

# ПАМЯТЬ VDS. 17.08.2026 машина встала целиком от нехватки RAM, и позиция осталась
# без управления 86 минут. Сторож ВНУТРИ агента при этом не молчал: он отправил 62
# предупреждения за двое суток, с 11:01 до 19:33 — каждые полчаса. Все ушли в
# Telegram со степенью «предупреждение», то есть в канал, который человек не
# смотрит непрерывно. Здесь тот же факт попадает в SMS — единственный канал,
# который будит. Порог 700 МБ выше агентского (400 МБ) НАМЕРЕННО: память на этой
# машине уходит примерно по 30 МБ в минуту, 400 МБ это меньше пятнадцати минут
# форы, а один только старт QUIK занимает двадцать.
mem = ((h.get("vds") or {}).get("mem") or {})
avail = mem.get("avail_mb")
if isinstance(avail, (int, float)) and avail > 0:
    checked.append("память VDS")
    # Оператору нужен СРОК, а не факт: скорость расхода считаем по прошлому
    # замеру этого же пробника. Файл рядом с pnl-снапшотом, формат тот же.
    _snap = os.path.expanduser("~/.stl-vdsmem-snapshot.json")
    _eta = ""
    try:
        with open(_snap, encoding="utf-8") as _sf:
            _prev = json.load(_sf)
        _dt = (now - _prev.get("ts_ms", 0)) / 60000.0
        _drop = _prev.get("avail_mb", 0) - avail
        if 0.5 <= _dt <= 60 and _drop > 0:
            _rate = _drop / _dt
            if _rate >= 1:
                _eta = " (уходит ~%.0f МБ/мин, до нуля ~%.0f мин)" % (_rate, avail / _rate)
    except Exception:
        pass
    try:
        with open(_snap, "w", encoding="utf-8") as _sf:
            json.dump({"ts_ms": now, "avail_mb": avail}, _sf)
    except Exception:
        pass
    if avail < 400 or mem.get("low_memory"):
        problems.append(("vds_mem_crit",
                         "ПАМЯТЬ VDS НА ИСХОДЕ: свободно %.0f МБ%s. Машина уже вставала "
                         "от этого, позиция остаётся без управления." % (avail, _eta)))
    elif avail < 1500:
        problems.append(("vds_mem",
                         "память VDS кончается: свободно %.0f МБ%s." % (avail, _eta)))
_qs = (h.get("vds") or {}).get("quik_state")
if _qs and _qs not in ("OK", "DISABLED"):
    problems.append(("quik_state", "QUIK на VDS в состоянии %s." % _qs))

# QUIK ЗАВИС — ПО ФАКТУ ДАННЫХ, А НЕ ПО ДОКЛАДУ ГАРДА (26.09.2026).
# 25.09 терминал перестал отвечать в 16:29, торговля встала на семь часов, и SMS
# НЕ ушла: проверка выше смотрит на quik_state от VDS-гарда, а гард в тот день
# ничего не отдавал — блок vds приехал пустым, условие не сработало вовсе.
# Здесь судим по самому надёжному признаку: биржа торгует, а данные не идут.
# Источник — снимок правды (trader/quik/truth.py), он пишется раз в две секунды
# и знает возраст КАЖДОГО кадра ленты.
try:
    _mss = get("/api/v1/quik/market-session")
except Exception:
    _mss = {}
if _mss.get("open") is True:
    try:
        with open("/home/ubuntu/apps/shectory-trader/data/truth.json", encoding="utf-8") as _fh:
            _truth = json.load(_fh)
    except Exception:
        _truth = {}
    _feed = [f for f in (_truth.get("feed") or []) if f.get("code") in
             ("RIZ6", "SiZ6", "GDZ6", "BRZ6")]
    _ages = [int(f.get("age_ms") or 0) / 1000 for f in _feed]
    _frozen_sec = int(min(_ages)) if _ages else None
    _link_sec = int((_truth.get("link_age_ms") or 0) / 1000)
    _limit = int(THR.get("frozen_sec", 300) or 300)
    if _frozen_sec is not None and _frozen_sec >= _limit:
        problems.append(("quik_frozen",
            "QUIK ЗАВИС! Биржа торгует, а данные не идут %d мин. Роботы работают "
            "вслепую, позиция без управления. Проверь терминал на VDS." %
            (_frozen_sec // 60)))
    elif not _feed and _link_sec >= _limit:
        problems.append(("quik_frozen",
            "QUIK ЗАВИС! Биржа торгует, а от агента нет данных %d мин. Позиция "
            "без управления. Проверь VDS." % (_link_sec // 60)))

# The 2026-07-21 freezer: daily order cap. Alert with runway; at 100% every robot
# order (exits included) is silently guard-rejected.
used, cap = h.get("daily_orders_used"), h.get("daily_orders_cap")
if isinstance(used, int) and isinstance(cap, int) and cap > 0:
    if used >= cap:
        problems.append(("cap_full", f"ЛИМИТ ОРДЕРОВ ИСЧЕРПАН {used}/{cap}: роботы НЕ МОГУТ торговать (включая выходы)!"))
    elif used >= int(cap * THR["cap_warn_pct"] / 100):
        problems.append(("cap_near", f"лимит ордеров почти исчерпан: {used}/{cap} (>={THR['cap_warn_pct']}%)."))

# Tape replay poisoning (2026-07-21 night: after a QUIK restart the tape replays the
# whole day; bars carry stale prices while quotes are live -> averaging robots piled
# real longs 1/min). Only matters while a real, unpaused robot CAN act on the bars.
active_real = [r for r in als.get("robots") or []
               if r.get("mode") == "real" and r.get("running") and not r.get("paused")]
# Session gate for the tape checks: PRE-OPEN the last trade is legitimately
# yesterday's (huge lag) and bars are yesterday's vs possibly-gapped quotes —
# both checks would false-fire and auto-pause every morning. In-session only.
# Session gate v2: ask the STL market-session oracle (MOEX ISS SYSTIME) whether
# the exchange TRADES RIGHT NOW. A frozen tape while the market is closed
# (night / weekend pause / holiday / intraday clearing) is EXPECTED, not a QUIK
# failure — the calendar window below stays only as a fallback when the oracle
# is unreachable (open=None), which is treated protectively (keep checking).
_lt = time.localtime()
_mins = _lt.tm_hour * 60 + _lt.tm_min
in_session = (7 * 60 + 2) <= _mins <= (23 * 60 + 50)
try:
    _ms = get("/api/v1/quik/market-session")
    if _ms.get("open") is False:
        in_session = False           # биржа закрыта по факту ISS: лаг ленты — норма
    elif _ms.get("open") is True:
        in_session = True            # биржа торгует: проверяем независимо от часов
except Exception:
    pass                             # оракул недоступен -> старое окно (защитно)
if active_real and in_session:
    checked.append("лаг тейпа")
    checked.append(f"бары vs рынок ({len(active_real)} реал-роботов)")
    lag = h.get("exchange_lag_ms")
    # tape_lag decided AFTER the divergence scan below (needs _diverged); see there.
    # Bars-vs-market divergence. FALSE-POSITIVE TRAP (2026-07-25 сб, thin Brent):
    # BRU6 trades sparsely on a Saturday, so a tape-built bar carried a ~1-min-old
    # trade price while bid/ask drifted ~1 pt — |close-mid| crossed 0.5% and the
    # watchdog auto-paused a REAL robot for the rest of the day, on a fresh tape
    # (lag ~13s, NOT the hours-long lag a real replay causes). Two gates now guard
    # the auto-pause: (a) the divergence must SURVIVE a re-read (a thin-market blip
    # resolves when the next trade prints or the quote drifts back; replay poison
    # stays wrong for minutes), and (b) the tape must be genuinely lagged — real
    # poison ALWAYS rides a lagged tape, so a fresh-tape divergence is alert-only.
    def _diverged(als_obj):
        hh = als_obj.get("health") or {}
        fd = {f.get("code"): f for f in hh.get("feed") or []}
        out = {}
        for rr in als_obj.get("robots") or []:
            if not (rr.get("mode") == "real" and rr.get("running") and not rr.get("paused")):
                continue
            if now / 1000 - (rr.get("last_bar_unix") or 0) > 300:
                continue  # bar not current -> not the poison signature
            try:
                sg = json.loads(rr.get("signal_json") or "{}")
            except Exception:
                continue
            lc, f = sg.get("last_close"), fd.get(rr.get("symbol"))
            if not lc or not f:
                continue
            bid, ask = f.get("bid") or 0, f.get("ask") or 0
            if bid and ask:
                mid = (bid + ask) / 2
                thr = max(THR["bars_atr_mult"] * (sg.get("atr") or 0), mid * THR["bars_pct"] / 100)
                if abs(lc - mid) > thr:
                    out[rr.get("id") or "?"] = (rr, lc, mid)
        return out

    poisoned = []
    cand = _diverged(als)
    # tape_lag: alert ONLY if the tape is lagged AND bars actually diverge from the
    # live quote (the real replay signature). A lagged tape with matching bars is a
    # quiet market (sparse Saturday-evening trades) -> log-only, never SMS/pause.
    if isinstance(lag, (int, float)) and lag > THR["tape_lag_sec"] * 1000:
        if cand:
            problems.append(("tape_lag",
                f"тейп QUIK отстаёт на {int(lag/1000)}с И бары разошлись — похоже на реплей, проверь роботов!"))
        else:
            problems.append(("tapelagquiet",
                f"тейп QUIK отстаёт на {int(lag/1000)}с, но бары совпадают с котировкой — тихий рынок, не реплей (без действий)."))
    if cand:
        time.sleep(THR.get("order_recheck_sec", 15) or 15)  # re-read window
        try:
            als2 = get("/api/v1/quik/agent-local-status")
        except Exception:
            als2 = als  # can't re-check -> keep the first read (protective)
        lag2 = (als2.get("health") or {}).get("exchange_lag_ms")
        # Real replay always rides a lagged tape; require it (default 60s floor,
        # tunable via thresholds.bars_min_lag_sec) so a FRESH-tape thin-market
        # blip never auto-pauses a real robot.
        lag_floor_ms = (THR.get("bars_min_lag_sec", 60) or 60) * 1000
        still = _diverged(als2)
        for rid_full, (rr, lc, mid) in still.items():
            if rid_full not in cand:
                continue  # blip resolved on the first re-read
            rid = rid_full[:12]
            if isinstance(lag2, (int, float)) and lag2 >= lag_floor_ms:
                poisoned.append(rr)
                problems.append((f"bars_{rid}",
                    f"бары {rid} разошлись с рынком: close {lc:.0f} vs {mid:.0f}, "
                    f"держится, лента отстаёт {int(lag2/1000)}с — похоже на реплей!"))
            else:
                # persistent divergence but a FRESH tape: not replay, alert only
                problems.append((f"barsfresh_{rid}",
                    f"бары {rid} отличаются от котировки (close {lc:.0f} vs {mid:.0f}), "
                    f"но лента свежая ({int((lag2 or 0)/1000)}с) — тонкий рынок, не реплей; пауза НЕ ставится."))

    # PROTECTIVE AUTO-PAUSE (operator-authorized 2026-07-21 "запускай сам если всё ок"
    # + its inverse): on tape replay / bars divergence, pause the affected real robots
    # NOW — blocks new orders only, positions untouched. Both switches are operator
    # config (autopause_tape_lag / autopause_bars). Paused ids are recorded in
    # ~/.stl-autopaused so ONLY these are auto-resumed by stl-morning-resume.sh under
    # its health gate — an operator's own manual pause is never auto-undone.
    to_pause = []
    if cfg["autopause_tape_lag"] and any(k == "tape_lag" for k, _ in problems):
        to_pause = active_real
    elif cfg["autopause_bars"] and poisoned:
        to_pause = poisoned
    if to_pause:
        import urllib.request as _rq
        marker = os.path.expanduser("~/.stl-autopaused")
        seen = set()
        if os.path.exists(marker):
            seen = {l.strip() for l in open(marker) if l.strip()}
        for r in to_pause:
            rid_full = r.get("id") or ""
            try:
                req = _rq.Request(
                    "http://localhost:8000/api/v1/quik/robots/%s/pause-agent" % rid_full,
                    data=b"{}", method="POST",
                    headers={"Authorization": "Bearer " + os.environ["TK"],
                             "Content-Type": "application/json"})
                _rq.urlopen(req, timeout=10)
                if rid_full not in seen:
                    with open(marker, "a") as mf:
                        mf.write(rid_full + "\n")
                problems.append((f"paused_{rid_full[:8]}",
                    f"робот {rid_full[:12]} ПОСТАВЛЕН НА ПАУЗУ автоматически (защита от ложных баров)."))
            except Exception as e:
                problems.append((f"pausefail_{rid_full[:8]}",
                    f"НЕ СМОГ поставить {rid_full[:12]} на паузу ({type(e).__name__}) — вмешайся!"))

# Stale heartbeat of a RUNNING robot (reports come every ~5-10s).
_running = [r for r in als.get("robots") or [] if r.get("running")]
checked.append(f"heartbeat ({len(_running)} роботов)")
for r in _running:
    hb = r.get("heartbeat_unix_ms") or 0
    if hb and (now - hb) / 1000 > THR["hb_sec"]:
        rid = (r.get("id") or "?")[:12]
        problems.append((f"hb_{rid}", f"робот {rid} без heartbeat {int((now-hb)/1000)}с."))

# Robot-order divergence (ROBOT_ORPHAN / MISSING) — real align material, not the
# benign record-fill trade transient, so only orders_ok flips this.
# ANTI-RACE (2026-07-22 false alert): recon legitimately reads "runner believes the
# order is working, QUIK shows it done" for the 2-5s window between a QUIK fill and
# the runner's book update — an averaging robot opens that window every bar. A
# divergence must SURVIVE a re-read (order_recheck_sec) before it is worth an SMS.
checked.append(f"сверка заявок ({len((als.get('recon') or {}).get('robot_checks') or [])} реал-роботов)")
_diverged = [c.get("id") or "?" for c in (als.get("recon") or {}).get("robot_checks") or []
             if not c.get("orders_ok", True)]
if _diverged:
    time.sleep(THR["order_recheck_sec"])
    try:
        als2 = get("/api/v1/quik/agent-local-status")
        still = {c.get("id") for c in (als2.get("recon") or {}).get("robot_checks") or []
                 if not c.get("orders_ok", True)}
    except Exception:
        still = set(_diverged)  # can't re-check -> keep the alert, better noisy than blind
    for rid_full in _diverged:
        if rid_full in still:
            rid = rid_full[:12]
            problems.append((f"ord_{rid}", f"робот {rid}: заявки расходятся с QUIK (orphan/missing), держится >{THR['order_recheck_sec']}с."))

# Backtest hung: a run stuck in 'running' past backtest_stuck_sec. A wedged fvg
# sweep once pegged the i9 for 12h with zero alert (2026-07-22) — this SMSes the
# operator so they can kill+restart the agent. No auto-action, escalation only.
_bt_n = int(THR.get("backtest_stuck_sec", 3600))
checked.append(f"зависшие бэктесты (>{_bt_n}с)")
try:
    for _r in get(f"/api/v1/agent/stuck-backtests?sec={_bt_n}").get("stuck", []):
        problems.append(("backtest_stuck",
                         f"Бэктест {_r['id']} считается дольше {_bt_n} секунд."))
except Exception:
    pass  # эндпоинт недоступен -> прочие проверки не роняем

# Escalation filter: a category the operator muted is NOT printed (smain never
# SMSes it) but IS logged below, marked, so the page still shows the finding.
for key, text in problems:
    if esc_on(key):
        print(f"{key}|{text}")

# Activation log for the STL watchdog-log page: one JSONL record per probe run.
# resolved = auto-actions the probe took itself (auto-pause); unresolved = найдено
# минус решённое — то, что ждёт оператора или самолечения.
try:
    _lp = os.path.expanduser("~/stl-watchdog-runs.jsonl")
    _n = 0
    if os.path.exists(_lp):
        with open(_lp, "rb") as _f:
            try:
                _f.seek(-4096, 2)
            except OSError:
                _f.seek(0)
            _lines = _f.read().decode("utf-8", "replace").strip().splitlines()
        if _lines:
            _n = json.loads(_lines[-1]).get("n", 0)
    def _mark(k, t):
        return t if esc_on(k) else t + " [SMS отключена настройкой]"
    _resolved = [_mark(k, t) for k, t in problems if k.startswith("paused_")]
    _unresolved = [_mark(k, t) for k, t in problems if not k.startswith("paused_")]
    with open(_lp, "a", encoding="utf-8") as _f:
        _f.write(json.dumps({
            "n": _n + 1, "ts_ms": int(time.time() * 1000),
            "checked": checked,
            "found": [_mark(k, t) for k, t in problems],
            "resolved": _resolved,
            "unresolved": _unresolved,
            # Загрузка ОБЕИХ машин в одной записи: хостер меряет себя сам, а smain
            # передаёт свою в SMAIN_LOAD при вызове пробника (22.09.2026, просьба
            # оператора видеть обе в компаньоне).
            "hoster_load": (round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None),
            "smain_load": (float(os.environ["SMAIN_LOAD"]) if os.environ.get("SMAIN_LOAD") else None),
        }, ensure_ascii=False) + "\n")
except Exception:
    pass  # логирование не должно валить probe
PYEOF

# СТОРОЖ ПОЗИЦИИ (17.08.2026). Вера робота против журнала сделок: recon позицию
# не сравнивает ни с чем по построению, и расхождение «верю 0, на бирже шорт 9»
# нашёл человек с экселем, а не софт. Печатает свои строки «ключ|текст» в тот же
# stdout — сторож smain разошлёт SMS с кулдауном по ключу, как для прочих.
PYTHONPATH=~/apps/shectory-trader "$PY" ~/apps/shectory-trader/scripts/position_guard.py 2>/dev/null || true
