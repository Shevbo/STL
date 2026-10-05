#!/usr/bin/env python3
"""Сборщик данных «Витрины кампаний бэктеста» (docs/campaign-showcase-spec.md).

Читает docs/campaigns/registry.json + Postgres (optimization_leaderboard, backtest_results,
agent_tasks), пишет data/campaign_showcase/index.json и <slug>.json. Только чтение БД,
идемпотентен, запись атомарна (tmp + rename).

ЗАПУСК (хостер):
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PY=$(ls -d ~/.cache/pypoetry/virtualenvs/shectory-trader-*/bin/python | head -1)
    PYTHONPATH=. $PY scripts/campaign_showcase_build.py

Запросы лёгкие: группировка по индексу (campaign_run, net_profit), LATERAL «лучшая строка»,
бэкфилл по индексу run_id; jsonb trades не трогаем, equity_curve читаем только у отобранных лидеров.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import fnmatch
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from scripts import showcase_volume as sv  # noqa: E402
REGISTRY = os.path.join(ROOT, "docs", "campaigns", "registry.json")
OUT_DIR = os.path.join(ROOT, "data", "campaign_showcase")
THUMB_N, CURVE_N, BF_MAX = 200, 1500, 10
LEADERS_N = 8
TOP_N, INLINE_N = 100, 10  # лидеров в отчёте / сколько из них с кривыми внутри файла карточки
NO_DESC = "описание не заполнено"
UNFINISHED_RUN = ("claimed", "running")


# ---------- чистые функции (покрыты тестами) ----------

def downsample(pts: list, n: int) -> list:
    """≤ n точек: первая, последняя и min/max каждого интервала (экстремумы живы)."""
    if len(pts) <= n:
        return list(pts)
    k = max((n - 2) // 2, 1)
    size = len(pts) / k
    keep = {0, len(pts) - 1}
    for b in range(k):
        lo, hi = int(b * size), int((b + 1) * size)
        seg = range(lo, max(hi, lo + 1))
        keep.add(min(seg, key=lambda i: pts[i][1]))
        keep.add(max(seg, key=lambda i: pts[i][1]))
    return [pts[i] for i in sorted(keep)]


def max_drawdown(curve: list) -> float | None:
    if not curve:
        return None
    peak, dd = curve[0][1], 0.0
    for _, y in curve:
        peak = max(peak, y)
        dd = max(dd, peak - y)
    return round(dd, 2)


def equity_to_curve(eq: list | None) -> list:
    """[{time, equity}] -> [[ts, pnl от первой точки]]."""
    if not eq:
        return []
    base = eq[0]["equity"]
    return [[int(p["time"]), round(p["equity"] - base, 2)] for p in eq]


def days_to_curve(days: list, nets: list) -> list:
    """Посуточный net -> накопленная кривая; метка = полночь UTC торгового дня."""
    out, acc = [], 0.0
    for d, v in zip(days, nets):
        acc += v
        ts = int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp())
        out.append([ts, round(acc, 2)])
    return out


def match_any(name: str, patterns: list) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns or [])


def auto_key(run: str) -> str:
    """Префикс автозаведённой группы: дата + буквенный префикс имени (шарды сливаются)."""
    if re.fullmatch(r"opt-\d{8}-\d+", run):  # широкие ночные перебори: одна линия, прогоны = разновидности
        return "opt"
    m = re.match(r"^camp-(\d{8})-(.*)$", run)
    if not m:
        return re.sub(r"\W+", "-", run).strip("-").lower()
    date, rest = m.groups()
    rest = re.sub(r"^camp\d{8}", "", rest)
    letters = re.match(r"[A-Za-z]+", rest)
    return f"{(letters.group(0) if letters else 'run').lower()}-{date}"


def _slug(x) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(x or "").lower()).strip("-")


_CONTRACT = re.compile(r"^([A-Za-z0-9]+?)[FGHJKMNQUVXZ]\d$")


def instrument(sym: str | None) -> str:
    """RIU6 -> RI, SiM6 -> Si, BRN6 -> BR; без кода контракта - как есть."""
    m = _CONTRACT.match(sym or "")
    return m.group(1) if m else (sym or "")


def group_key(r: dict) -> str:
    """Карточка = одна логика (стратегия) + один инструмент в рамках одной кампании-линии."""
    return "-".join(x for x in (auto_key(r["campaign_run"]), _slug(r.get("strategy")) or "s",
                                _slug(instrument(r.get("symbol"))) or "x") if x)


_MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
           "октябрь", "ноябрь", "декабрь"]


def window_label(frm: str | None, to: str | None, fallback_date: str | None = None) -> str:
    """'2026-07-01','2026-07-30' -> 'июль 2026'; 'июнь–июль 2026'; нет дат - дата кампании."""
    try:
        f, t = dt.date.fromisoformat(frm), dt.date.fromisoformat(to)
    except (TypeError, ValueError):
        if fallback_date and re.fullmatch(r"\d{8}", fallback_date):
            return f"{fallback_date[6:]}.{fallback_date[4:6]}.{fallback_date[:4]}"
        return "окно не указано"
    if (f.year, f.month) == (t.year, t.month):
        return f"{_MONTHS[f.month - 1]} {f.year}"
    if f.year == t.year:
        return f"{_MONTHS[f.month - 1]}–{_MONTHS[t.month - 1]} {f.year}"
    return f"{_MONTHS[f.month - 1]} {f.year}–{_MONTHS[t.month - 1]} {t.year}"


def _clip(text: str, n: int = 160) -> str:
    text = re.split(r"\s\((?![^)]*\))", text.strip())[0].strip(" .;,")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


_STRAT_CACHE: dict = {}


def strategy_info(sid: str | None) -> tuple[str, str]:
    """-> (имя, идея в одну строку). Источник: trader/lab/strategies (library: REGISTRY+STRATEGY_DESC,
    остальные модули: первая строка docstring через ast, без импорта). Неизвестна - id и NO_DESC."""
    if not sid:
        return "стратегия не указана", NO_DESC
    if sid in _STRAT_CACHE:
        return _STRAT_CACHE[sid]
    inv = sid.endswith("__inv")
    base = sid[:-5] if inv else sid
    name, idea = base, NO_DESC
    first = None
    try:
        from trader.lab.strategies import library as lib
        if base in lib.REGISTRY:
            name = lib.REGISTRY[base]["name"]
            first = (lib.STRATEGY_DESC.get(base) or "").split(chr(10))[0]
    except Exception:  # noqa: BLE001 - витрина не должна падать из-за импорта движка
        pass
    if first is None and re.fullmatch(r"[a-z0-9_]+", base):
        path = os.path.join(ROOT, "trader", "lab", "strategies", base + ".py")
        if os.path.exists(path):
            import ast
            try:
                doc = ast.get_docstring(ast.parse(open(path, encoding="utf-8").read())) or ""
                first = doc.strip().split(chr(10))[0]
                if " — " not in first:  # нет «Имя — суть»: имя из первой строки, сути нет
                    name, first = _clip(re.split(r"\s\(", first)[0], 60) or base, None
            except (SyntaxError, OSError):
                first = None
    if first and " — " in first:
        n_, i_ = first.split(" — ", 1)
        if name == base:
            name = n_.strip()
        idea = _clip(i_) or NO_DESC
    if inv:
        name = f"{name} (инверсия)"
        idea = "Зеркальный сигнал: " + (idea if idea != NO_DESC else "обратные сделки базовой стратегии")
    _STRAT_CACHE[sid] = (name, idea)
    return name, idea


def auto_title(rs: list, key: str) -> str:
    sid = next((r["strategy"] for r in rs if r.get("strategy")), None)
    syms = sorted({r["symbol"] for r in rs if r.get("symbol")})
    inst = sorted({instrument(x) for x in syms})
    where = syms[0] if len(syms) == 1 else (inst[0] if len(inst) == 1 else "/".join(inst[:3]) or "?")
    frm = min((r["date_from"] for r in rs if r.get("date_from")), default=None)
    to = max((r["date_to"] for r in rs if r.get("date_to")), default=None)
    date = re.search(r"-(\d{8})(?:-|$)", "-" + key + "-")
    return f"{strategy_info(sid)[0]} · {where} · {window_label(frm, to, date.group(1) if date else None)}"


def varieties_of(runs: list) -> list:
    """Разновидности внутри карточки: (контракт, окно) -> прогоны (campaign_run) и лучшая строка."""
    by: dict = {}
    for r in runs:
        k = (r.get("symbol"), r.get("date_from"), r.get("date_to"))
        by.setdefault(k, []).append(r)
    out = []
    for (sym, frm, to), rs in sorted(by.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        best = max(rs, key=lambda r: r.get("net") if r.get("net") is not None else -1e18)
        names = sorted({r["campaign_run"] for r in rs})
        out.append({"label": f"{sym or '?'} · {window_label(frm, to)}", "n_runs": len(names),
                    "campaign_runs": names[:200],
                    "best": {"net": best.get("net"), "trades": best.get("trades"),
                             "max_dd": best.get("max_dd"), "params": best.get("params"),
                             "campaign_run": best["campaign_run"]}})
    return out


def merge_cards(registry: list, runs: list) -> list:
    """Реестр приоритетнее автозаведённых: прогоны, занятые реестром, группы не образуют.

    Возвращает список заготовок {entry, runs, auto}; runs - лучшие строки по (campaign_run, стратегия, символ).
    """
    claimed: set = set()
    cards = []
    for e in registry:
        mine = [r for r in runs if match_any(r["campaign_run"], e.get("campaign_runs"))]
        claimed.update(r["campaign_run"] for r in mine)
        cards.append({"entry": e, "runs": mine, "auto": False})
    groups: dict = {}
    for r in runs:
        if r["campaign_run"] not in claimed:
            groups.setdefault(group_key(r), []).append(r)
    taken = {c["entry"]["slug"] for c in cards}
    for key in sorted(groups):
        rs = groups[key]
        slug = key if key not in taken else f"{key}-auto"
        taken.add(slug)
        sid = next((r["strategy"] for r in rs if r.get("strategy")), None)
        names = sorted({r["campaign_run"] for r in rs})
        cards.append({"entry": {
            "slug": slug, "title": auto_title(rs, key), "idea": strategy_info(sid)[1],
            "strategy": sid, "family": slug, "rev": 1, "parent": None, "changes": None,
            "campaign_runs": names, "doc": None, "status_hint": "done", "verdict": None,
            "unit": "rub", "kind": "optimizer",
        }, "runs": rs, "auto": True})
    return cards


def status_of(tasks: list, has_curve: bool, has_results: bool, hint: str | None):
    """-> (status, progress). Правила спеки: running/queued из agent_tasks, иначе done/no_curve."""
    if tasks:
        fin = sum(t["status"] in ("done", "failed") for t in tasks)
        prog = {"finished": fin, "total": len(tasks)}
        if fin < len(tasks):
            if any(t["status"] in UNFINISHED_RUN for t in tasks):
                return "running", prog
            if fin == 0:
                return "queued", prog
            return "running", prog
    else:
        prog = None
    if has_curve:
        return "done", prog
    if has_results or tasks or hint == "done":
        return "no_curve", prog
    return "queued", prog


# ---------- адаптеры кривых исследовательских линий ----------

def curve_sweep(results: dict) -> list:
    """grid_sim mode=sweep, phase=test: лидер 'sel' по каждому инструменту-окну."""
    leaders = []
    for tid, res in sorted(results.items()):
        for ent in res or []:
            if ent.get("phase") != "test" or "test_days" not in ent:
                continue
            for r in ent.get("results", []):
                if r.get("tag") != "sel":
                    continue
                leaders.append({
                    "params": {"instrument": ent.get("instrument"), "window": ent.get("window"), **r["vec"]},
                    "metrics": {"net_taker": r.get("net_t"), "net_maker": r.get("net_m"),
                                "cycles": r.get("cycles"), "fills": r.get("fills")},
                    "trades_n": r.get("fills"),
                    "curve": days_to_curve(ent["test_days"], r["day_net"]),
                    "_net": r.get("net_t") or 0.0,
                })
    leaders.sort(key=lambda x: -x["_net"])
    return leaders


def curve_nextday(results: dict, config: str) -> list:
    """grid_sim mode=nextday_days: конфиг на отложенной трети, net тейкер = gross - fee_t."""
    leaders = []
    for tid, res in sorted(results.items()):
        for ent in res or []:
            days = (ent.get("dates") or {}).get("test")
            for cf in ent.get("configs", []):
                if cf.get("name") != config or not days:
                    continue
                t = cf["test"]
                nets = [g - f for g, f in zip(t["gross"], t["fee_t"])]
                leaders.append({
                    "params": {"symbol": ent.get("symbol"), "param_set": cf.get("pi"), "config": config},
                    "metrics": {"net_taker": round(sum(nets), 1), "fires": t.get("fires"),
                                "stops": t.get("stop")},
                    "trades_n": t.get("fires"),
                    "curve": days_to_curve(days, nets),
                    "_net": sum(nets),
                })
    leaders.sort(key=lambda x: -x["_net"])
    return leaders


# ---------- сборка карточек ----------

def run_unit(point_value) -> str:
    """Движок считает pnl = пункты x point_value; без коэффициента (старые кампании) или при 1.0 - пункты."""
    return "points" if point_value in (None, 1, 1.0) else "rub"


def _window(frm, to) -> str | None:
    return f"{frm}..{to}" if frm and to else None


def bf_drift_note(rows: list) -> str | None:
    """Бэкфилл считан на ТЕКУЩЕМ движке; net строки лидерборда мог быть получен на старом. Расхождение > 1% - в notes."""
    bad = [r for r in rows if r.get("lb_net") is not None and r.get("net") is not None
           and abs(r["net"] - r["lb_net"]) > 0.01 * max(abs(r["lb_net"]), 1.0)]
    if not bad:
        return None
    r = bad[0]
    return (f"кривые лидеров = перепрогон на текущем движке; net расходится с лидербордом у {len(bad)} из "
            f"{len(rows)} (лидер: {round(r['net'])} против {round(r['lb_net'])})")


def bf_belongs(row: dict, runs: list) -> bool:
    """bf-строка идёт в карточку своей логики: инструмент (и стратегия, если известна) совпадают с
    её прогонами. Старые бэкфиллы без стратегии: принимаем, только если у кампании одна стратегия."""
    mine = [r for r in runs if r["campaign_run"] == row["run_id"].rsplit("-bf", 1)[0]]
    sym = (row.get("params") or {}).get("symbol") or row.get("symbol")
    if sym and all(instrument(r.get("symbol")) != instrument(sym) for r in mine):
        return False
    st = row.get("strategy")
    if st:
        return any(r.get("strategy") == st for r in mine)
    return len({r.get("strategy") for r in mine}) <= 1


def leader_sort_key(ld: dict):
    sc, net = ld.get("score"), ld.get("net")
    return (sc is None, -(sc or 0.0), net is None, -(net or 0.0))


def _std(ld: dict) -> dict:
    for k in ("contracts_peak", "full_cost_rub", "return_pct", "buyhold_curve", "rf", "l_share",
              "l_share_source", "score", "unit", "unit_source", "curve_url"):
        ld.setdefault(k, None)
    ld.setdefault("rev", 1)
    return ld


def make_bf_leader(r: dict, ctx, card_unit: str) -> dict:
    """Лидер с перепрогоном (бэкфилл): честный объём по сделкам, buyhold из баров, L по кривой."""
    curve, params = r["curve"], r.get("params") or {}
    sym = params.get("symbol")
    peak = r.get("peak")  # (контрактов, цена, время) по сделкам перепрогона
    n_peak = peak[0] if peak else r.get("peak_col")
    pv = ctx.pv_at(sym, peak[2] if peak else curve[-1][0]) if ctx else None
    unit = sv.infer_unit(r.get("net"), r.get("gross_pts"), pv)
    net = r.get("net")
    net_rub = net * pv if (unit == "points" and net is not None) else (net if unit == "rub" else None)
    cost = sv.full_cost_rub(peak[0], peak[1], pv) if peak else None
    bh = ctx.buyhold(sym, curve[0][0], curve[-1][0], n_peak, unit == "rub", CURVE_N, downsample) \
        if (ctx and unit and n_peak) else None
    ls = sv.month_share(curve)
    rf = r.get("rf")
    return _std({
        "params": r.get("params"), "trades_n": r.get("trades"),
        "_src": {"campaign_run": r["run_id"].rsplit("-bf", 1)[0], "strategy": r.get("strategy"), "symbol": sym},
        "metrics": {"net": net, "trades": r.get("trades"), "max_dd_db": r.get("max_dd"),
                    "sharpe": r.get("sharpe"), "campaign_run": r["run_id"], "lb_net": r.get("lb_net"),
                    "rerun_note": "перепрогон на текущем движке", "recovery_factor": rf},
        "curve": downsample(curve, CURVE_N), "buyhold_curve": bh, "contracts_peak": n_peak,
        "full_cost_rub": cost, "return_pct": sv.return_pct(net_rub, cost), "unit": unit,
        "unit_source": "measured" if unit else None,
        "rf": rf, "net": net, "l_share": ls, "l_share_source": "curve" if ls is not None else None,
        "score": sv.score_of(rf, net, ls)})


def make_row_leader(r: dict, card_unit: str) -> dict:
    """Строка лидерборда без перепрогона: объёма и кривой нет (null), L из windows_profitable/windows_total."""
    wt = r.get("wt")
    ls = round(r["wp"] / wt, 4) if wt and r.get("wp") is not None else None
    rf, net = r.get("rf"), r.get("net")
    return _std({
        "params": r.get("params"), "trades_n": r.get("trades"),
        "_src": {"campaign_run": r["campaign_run"], "strategy": r.get("strategy"), "symbol": r.get("symbol")},
        "metrics": {"net": net, "trades": r.get("trades"), "max_dd_db": r.get("max_dd"),
                    "campaign_run": r["campaign_run"], "recovery_factor": rf},
        "curve": None, "rf": rf, "net": net, "unit": None, "unit_source": None, "l_share": ls,
        "l_share_source": "leaderboard_windows" if ls is not None else None,
        "score": sv.score_of(rf, net, ls)})


def finalize_leaders(slug: str, leaders: list) -> tuple[list, list]:
    """Сортировка по score, затем net; топ-100; кривые внутри файла только у топ-10, у остальных curve_url.
    -> (leaders, lazy-файлы {rank, curve, buyhold_curve})."""
    leaders = sorted((_std(ld) for ld in leaders), key=leader_sort_key)[:TOP_N]
    lazy = []
    for i, ld in enumerate(leaders):
        ld["rank"] = i + 1
        if i >= INLINE_N and ld.get("curve"):
            lazy.append({"rank": i + 1, "curve": ld["curve"], "buyhold_curve": ld["buyhold_curve"]})
            ld["curve"], ld["buyhold_curve"] = None, None
            ld["curve_url"] = f"{slug}.leader-{i + 1}.json"
    return leaders, lazy


def build_card(c: dict, tasks_all: list, bf: dict, task_results: dict, now_iso: str, ctx=None, tops=None):
    """-> (index_card, detail). bf: run -> [rows]; task_results: task_id -> result."""
    e = c["entry"]
    runs = c["runs"]
    tasks = [t for t in tasks_all
             if match_any(t["id"], e.get("task_ids")) or match_any(t["module"], e.get("task_modules"))]
    leaders, notes, reason = [], [], None
    unit = e.get("unit") or "rub"
    if runs:
        # «point_value None/1.0 = пункты» проверено против измеренной единицы (scripts/unit_heuristic_check.py):
        # ошибается в 7 из 15. Без перепрогона единица неизвестна: null (измеряется только у лидеров бэкфилла)
        unit = None

    # (1) бэктест-кривые лидеров оптимизатора: <campaign_run>-bf<rank>
    names = sorted({r["campaign_run"] for r in runs})
    rows = [r for n in names for r in bf.get(n, []) if bf_belongs(r, runs)]
    rows = sorted((r for r in rows if r.get("curve")), key=lambda r: -(r.get("net") or 0))[:LEADERS_N]
    for r in rows:
        leaders.append(make_bf_leader(r, ctx, unit))
    note = bf_drift_note(rows)
    if note:
        notes.append(note)
    # (2) исследовательские кривые из результатов i9
    spec = e.get("curve")
    if not leaders and spec and task_results:
        res = {t["id"]: task_results[t["id"]] for t in tasks if t["id"] in task_results}
        ls = curve_sweep(res) if spec["kind"] == "sweep" else curve_nextday(res, spec["config"])
        for ld in ls[:LEADERS_N * 2]:
            ld.pop("_net", None)
            ld["curve"] = downsample(ld["curve"], CURVE_N)
            ld["net"] = (ld.get("metrics") or {}).get("net_taker")
            ld["l_share"] = sv.month_share(ld["curve"])
            ld["l_share_source"] = "curve" if ld["l_share"] is not None else None
            leaders.append(ld)
        if spec.get("note"):
            notes.append(spec["note"])
    # (3) строки лидерборда без перепрогона (метрики без кривой): топ-100 карточки
    if runs and e.get("kind") != "research":
        src = (tops or {}).get(e["slug"])
        if src is None:  # нет потока из БД (тесты, ручной запуск): лучшие строки по прогонам
            src = sorted(runs, key=lambda r: -(r.get("net") if r.get("net") is not None else -1e18))[:3]
        have = {json.dumps(ld.get("params"), sort_keys=True) for ld in leaders}
        for r in src:
            if json.dumps(r.get("params"), sort_keys=True) not in have:
                leaders.append(make_row_leader(r, unit))
    elif not leaders and runs:
        for r in sorted(runs, key=lambda r: -(r.get("net") if r.get("net") is not None else -1e18))[:3]:
            leaders.append(make_row_leader(r, unit))
    leaders, lazy = finalize_leaders(e["slug"], leaders)
    srcs = [ld.pop("_src", None) for ld in leaders]
    wb_src = dict(srcs[0]) if leaders and srcs[0] else None
    if wb_src and not wb_src.get("strategy"):  # старые бэкфиллы без strategy: берём, если у кампании она одна
        st = {r.get("strategy") for r in runs if r["campaign_run"] == wb_src["campaign_run"]}
        wb_src["strategy"] = next(iter(st)) if len(st) == 1 else None
    if wb_src is not None:
        wb_src["params"] = leaders[0].get("params")
    lead, lead_full = None, None
    for ld in leaders:  # лидер с кривой: первый по score; кривая внутри или в lazy-файле
        if ld.get("curve"):
            lead, lead_full = ld, ld["curve"]
            break
        if ld.get("curve_url"):
            lead, lead_full = ld, next(z["curve"] for z in lazy if z["rank"] == ld["rank"])
            break
    has_curve = lead_full is not None
    if lead and lead.get("unit"):
        unit = lead["unit"]
    unit_source = "measured" if (lead and lead.get("unit_source") == "measured") else None
    status, progress = status_of(tasks, has_curve, bool(runs), e.get("status_hint"))
    if status == "no_curve":
        if runs and e.get("kind") == "research":
            reason = "в БД остались только строки лидерборда без посуточной кривой; нужен перепрогон на i9"
        elif runs:
            reason = "у кампании нет бэкфилла топ-N в backtest_results (<campaign_run>-bf<rank>); нужен досчёт"
        elif tasks or spec:
            reason = "в результатах i9 нет посуточного net выбранной конфигурации; нужен перепрогон"
        else:
            reason = "прогоны кампании в БД и очереди не сопоставлены с записью реестра"
    thumb = downsample(lead_full, THUMB_N) if has_curve else None
    lead_curve = lead_full
    top_run = max(runs, key=lambda r: r.get("net") if r.get("net") is not None else -1e18) if runs else None
    if has_curve:
        net = lead_curve[-1][1]
        trades = lead.get("trades_n")
        dd = max_drawdown(lead_curve)
        frm = dt.datetime.fromtimestamp(lead_curve[0][0], dt.timezone.utc).date().isoformat()
        to = dt.datetime.fromtimestamp(lead_curve[-1][0], dt.timezone.utc).date().isoformat()
        window = _window(frm, to)
    elif top_run and e.get("kind") == "optimizer":  # у gate-шардов лучшая строка лидерборда не итог линии
        net, trades, dd = top_run.get("net"), top_run.get("trades"), top_run.get("max_dd")
        window = _window(top_run.get("date_from"), top_run.get("date_to"))
        notes.append("max_dd из лидерборда как в БД (не из кривой)")
    else:
        net = trades = dd = window = None
    stamps = [r["created_at"] for r in runs if r.get("created_at")] + \
             [t["finished_at"] for t in tasks if t.get("finished_at")]
    card = {
        "slug": e["slug"], "title": e["title"], "idea": e.get("idea") or NO_DESC,
        "strategy": e.get("strategy"), "family": e.get("family") or e["slug"], "rev": e.get("rev", 1),
        "status": status, "progress": progress, "updated_at": max(stamps) if stamps else None,
        "headline": {"net": net, "trades": trades, "max_dd": dd, "window": window,
                     "comparable": unit is not None,  # net в известной единице
                     "contracts_peak": lead.get("contracts_peak") if has_curve else None,
                     "full_cost_rub": lead.get("full_cost_rub") if has_curve else None,
                     "return_pct": lead.get("return_pct") if has_curve else None},
        "thumb": thumb, "verdict": e.get("verdict"), "doc": e.get("doc"),
        "unit": unit, "unit_source": unit_source, "kind": e.get("kind") or "research",
        "no_curve_reason": reason,
    }
    varieties = varieties_of(runs)
    card["n_varieties"] = len(varieties)
    syms = e.get("symbols") or sorted({r["symbol"] for r in runs if r.get("symbol")}) or None
    card["symbols"] = syms
    detail = {
        **card, "leaders": leaders, "changes": e.get("changes"), "parent": e.get("parent"),
        "runs": names + [t["id"] for t in tasks], "varieties": varieties,
        "data_window": {"from": window.split("..")[0] if window else None,
                        "to": window.split("..")[1] if window else None, "symbols": syms},
        "notes": "; ".join(notes) or e.get("notes"), "auto": c["auto"], "_lazy": lazy, "_wb": wb_src,
    }
    return card, detail


def attach_revisions(details: list) -> None:
    fam: dict = {}
    for d in details:
        fam.setdefault(d["family"], []).append(d)
    for ds in fam.values():
        revs = [{"slug": d["slug"], "rev": d["rev"], "changes": d["changes"]}
                for d in sorted(ds, key=lambda d: d["rev"])]
        for d in ds:
            d["revisions"] = revs


def write_atomic(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def sort_cards(out: list) -> list:
    """done/running с кривой, затем queued, затем no_curve; внутри: research раньше optimizer, свежие выше."""
    def grp(c):
        if c["thumb"] and c["status"] in ("done", "running"):
            return 0
        return 1 if c["status"] == "queued" else 2
    out = sorted(out, key=lambda cd: cd[0]["updated_at"] or "", reverse=True)
    out = sorted(out, key=lambda cd: cd[0]["kind"] != "research")
    return sorted(out, key=lambda cd: grp(cd[0]))


def write_all(cards_details: list, out_dir: str, redirects: dict | None = None) -> None:
    keep = {"index.json", "slug_redirects.json"}
    for _, d in cards_details:
        body = {k: v for k, v in d.items() if k not in ("_lazy", "_wb")}
        write_atomic(os.path.join(out_dir, f"{d['slug']}.json"), body)
        keep.add(f"{d['slug']}.json")
        for z in d.get("_lazy") or []:  # кривые лидеров за пределами топ-10: ленивый файл
            name = f"{d['slug']}.leader-{z['rank']}.json"
            write_atomic(os.path.join(out_dir, name), z)
            keep.add(name)
    write_atomic(os.path.join(out_dir, "index.json"), [c for c, _ in cards_details])
    write_atomic(os.path.join(out_dir, "slug_redirects.json"), redirects or {})
    for f in os.listdir(out_dir):  # убрать карточки, исчезнувшие из реестра
        if f.endswith(".json") and f not in keep:
            os.unlink(os.path.join(out_dir, f))


def legacy_slug(run: str) -> str:
    """Slug автозаведённой карточки до резки по логике (то, что могли уже раздать ссылками)."""
    if re.fullmatch(r"opt-\d{8}-\d+", run):
        return re.sub(r"\W+", "-", run).strip("-").lower()
    return auto_key(run)


def build_redirects(cards_details: list) -> dict:
    """{старый slug: новый} для исчезнувших после резки. Одна старая карточка -> лучшая по score из новых
    (затем по net); остальные части перечислены в notes выбранной карточки."""
    live = {d["slug"] for _, d in cards_details}
    parts: dict = {}
    for _, d in cards_details:
        if not d.get("auto"):
            continue
        ld = d["leaders"][0] if d.get("leaders") else {}
        key = (ld.get("score") is not None, ld.get("score") or 0.0, d["headline"].get("net") or 0.0)
        for run in d["runs"]:
            parts.setdefault(legacy_slug(run), {})[d["slug"]] = key
    red, by_slug = {}, {d["slug"]: d for _, d in cards_details}
    for old in sorted(parts):
        if old in live:
            continue
        cand = parts[old]
        best = max(sorted(cand), key=lambda sl: cand[sl])
        red[old] = best
        others = sorted(x for x in cand if x != best)
        if others:
            det = by_slug[best]
            tail = ", ".join(others[:30]) + (f" и ещё {len(others) - 30}" if len(others) > 30 else "")
            add = f"бывшая карточка {old} разрезана по логике; остальные части: {tail}"
            det["notes"] = f"{det['notes']}; {add}" if det.get("notes") else add
    return red


def build(registry: list, runs: list, tasks: list, bf: dict, task_results: dict, now_iso: str,
          ctx=None, tops=None) -> list:
    out = [build_card(c, tasks, bf, task_results, now_iso, ctx, tops) for c in merge_cards(registry, runs)]
    attach_revisions([d for _, d in out])
    return sort_cards(out)


# ---------- БД ----------

def _j(v):
    return json.loads(v) if isinstance(v, str) else v


async def stream_tops(c, cards: list) -> dict:
    """Топ-TOP_N строк лидерборда на карточку одним проходом по таблице (курсор, в памяти только кучи).
    Ключ отбора тот же, что у сортировки лидеров: score = rf*net*L (L из windows_profitable/total), затем net."""
    import heapq
    slug_of = {}
    for cd in cards:
        if cd["entry"].get("kind") == "research":
            continue
        for r in cd["runs"]:
            slug_of[(r["campaign_run"], r["strategy"], r["symbol"])] = cd["entry"]["slug"]
    heaps: dict = {}
    async with c.transaction():
        async for x in c.cursor("""select id, campaign_run, strategy, symbol, net_profit, total_trades,
                max_drawdown, recovery_factor, windows_profitable, windows_total
                from optimization_leaderboard where net_profit is not null"""):
            slug = slug_of.get((x["campaign_run"], x["strategy"], x["symbol"]))
            if slug is None:
                continue
            net, rf, wt = x["net_profit"], x["recovery_factor"], x["windows_total"]
            ls = x["windows_profitable"] / wt if wt and x["windows_profitable"] is not None else None
            sc = sv.score_of(rf, net, ls)
            key = (1, sc) if sc is not None else (0, net)
            h = heaps.setdefault(slug, [])
            item = (key, x["id"], dict(campaign_run=x["campaign_run"], strategy=x["strategy"],
                                       symbol=x["symbol"], net=net, trades=x["total_trades"],
                                       max_dd=x["max_drawdown"], rf=rf, wp=x["windows_profitable"], wt=wt))
            if len(h) < TOP_N * 3:
                heapq.heappush(h, item)
            elif key > h[0][0]:
                heapq.heapreplace(h, item)
    tops, need = {}, []
    for slug, h in heaps.items():
        seen, rows = set(), []
        for key, rid, row in sorted(h, key=lambda t: t[0], reverse=True):
            k = (round(row["net"], 2), row["trades"])  # те же net и сделки = те же сделки
            if k in seen:
                continue
            seen.add(k)
            row["_id"] = rid
            rows.append(row)
            need.append(rid)
            if len(rows) == TOP_N:
                break
        tops[slug] = rows
    params: dict = {}
    for i in range(0, len(need), 5000):
        for x in await c.fetch("select id, params from optimization_leaderboard where id = any($1::bigint[])",
                               need[i:i + 5000]):
            params[x["id"]] = _j(x["params"])
    for rows in tops.values():
        for row in rows:
            row["params"] = params.get(row.pop("_id"))
    return tops


async def load_db(registry: list):
    import asyncpg
    c = await asyncpg.connect(os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql"))
    try:
        # лучшая строка на (кампания, стратегия, символ): карточка = одна логика + один инструмент
        rows = await c.fetch("""
            select distinct on (campaign_run, strategy, symbol)
                   campaign_run, net_profit, total_trades, max_drawdown, strategy,
                   symbol, params, point_value, date_from, date_to, created_at
            from optimization_leaderboard
            order by campaign_run, strategy, symbol, net_profit desc nulls last""")
        runs = [{"campaign_run": x["campaign_run"], "net": x["net_profit"],
                 "trades": x["total_trades"], "max_dd": x["max_drawdown"], "strategy": x["strategy"],
                 "symbol": x["symbol"], "params": _j(x["params"]), "point_value": x["point_value"],
                 "date_from": str(x["date_from"]) if x["date_from"] else None,
                 "date_to": str(x["date_to"]) if x["date_to"] else None,
                 "created_at": x["created_at"].isoformat() if x["created_at"] else None} for x in rows]
        trows = await c.fetch("select id, module, status, finished_at, created_at from agent_tasks")
        tasks = [{"id": x["id"], "module": x["module"], "status": x["status"],
                  "finished_at": (x["finished_at"] or x["created_at"]).isoformat()
                  if (x["finished_at"] or x["created_at"]) else None} for x in trows]
        # бэктест-кривые: только у прогонов, попавших в карточки
        cards = merge_cards(registry, runs)
        names = sorted({r["campaign_run"] for cd in cards for r in cd["runs"]})
        ids = [f"{n}-bf{i}" for n in names for i in range(BF_MAX)]
        have = await c.fetch("""select r.run_id, r.net_profit, r.total_trades, r.max_drawdown, r.sharpe,
                   b.strategy, r.extra::jsonb ->> 'lb_net' as lb_net
            from backtest_results r left join backtest_runs b on b.id = r.run_id
            where r.run_id = any($1::text[]) and jsonb_array_length(r.equity_curve) > 0""", ids)
        bf: dict = {}
        for x in sorted(have, key=lambda x: -(x["net_profit"] or 0)):
            run = x["run_id"].rsplit("-bf", 1)[0]
            bf.setdefault(run, []).append(dict(run_id=x["run_id"], net=x["net_profit"],
                                               trades=x["total_trades"], max_dd=x["max_drawdown"],
                                               sharpe=x["sharpe"], strategy=x["strategy"],
                                               lb_net=float(x["lb_net"]) if x["lb_net"] else None))
        # лидеры карточки: топ LEADERS_N по net; кривую читаем только им
        loaded: dict = {}
        for cd in cards:
            cnames = sorted({r["campaign_run"] for r in cd["runs"]})
            pool = sorted((r for n in cnames for r in bf.get(n, [])),
                          key=lambda r: -(r["net"] or 0))
            for r in pool:
                if "params" not in r:
                    x = await c.fetchrow("select params from backtest_results where run_id=$1", r["run_id"])
                    r["params"] = _j(x["params"])
            pool = [r for r in pool if bf_belongs(r, cd["runs"])][:LEADERS_N]
            for r in pool:
                if r["run_id"] not in loaded:
                    x = await c.fetchrow("""select equity_curve, trades, recovery_factor, peak_contracts
                        from backtest_results where run_id=$1""", r["run_id"])
                    tr = _j(x["trades"])
                    loaded[r["run_id"]] = (equity_to_curve(_j(x["equity_curve"])), sv.peak_from_trades(tr),
                                           sv.gross_points(tr), x["recovery_factor"], x["peak_contracts"])
                r["curve"], r["peak"], r["gross_pts"], r["rf"], r["peak_col"] = loaded[r["run_id"]]
        # результаты i9 для адаптеров кривых
        task_results: dict = {}
        for cd in cards:
            spec = cd["entry"].get("curve")
            if not spec:
                continue
            for t in tasks:
                if match_any(t["id"], spec.get("tasks")) and t["id"] not in task_results:
                    task_results[t["id"]] = _j(await c.fetchval(
                        "select result from agent_tasks where id=$1 and status='done'", t["id"]))
        tops = await stream_tops(c, cards)
        meta = {}
        for x in await c.fetch("select symbol, point_value, price_step, price_step_value from instrument_meta"):
            pv = x["point_value"] or (x["price_step_value"] / x["price_step"]
                                      if x["price_step_value"] and x["price_step"] else None)
            if pv:
                meta[x["symbol"]] = float(pv)
        return runs, tasks, bf, task_results, tops, meta
    finally:
        await c.close()


def apply_workbench(cards_details: list, jobs: dict, ctx=None) -> None:
    """workbench_base у optimizer-карточек: скрипт и окно из job_body кампании ИМЕННО стратегии лидера №1,
    base_params = params его строки. Нельзя достать честно - None и причина в notes. У research поля нет."""
    for _, d in cards_details:
        src = d.pop("_wb", None)
        if d.get("kind") != "optimizer":
            continue
        why, wb = None, None
        if not src or not src.get("strategy"):
            why = "нет лидера или стратегия лидера не определена"
        else:
            job = jobs.get((src["campaign_run"], src["strategy"]))
            if not job or not job.get("scriptCode"):
                why = (f"нет job_body кампании {src['campaign_run']} со стратегией {src['strategy']} "
                       "(opt-* без job_body или стратегия не совпала)")
            else:
                sym = src.get("symbol") or (src.get("params") or {}).get("symbol") or job.get("symbol")
                pv = None
                if ctx and sym:
                    try:
                        ts = int(dt.datetime.fromisoformat(str(job["dateTo"])[:10]).replace(
                            tzinfo=dt.timezone.utc).timestamp())
                    except (KeyError, ValueError):
                        ts = None
                    pv = ctx.pv_at(sym, ts)
                wb = {"strategy": src["strategy"], "symbol": sym, "date_from": job.get("dateFrom"),
                      "date_to": job.get("dateTo"), "base_params": src.get("params"),
                      "script_code": job["scriptCode"], "point_value": pv}
        d["workbench_base"] = wb
        if why:
            d["notes"] = f"{d['notes']}; workbench_base: {why}" if d.get("notes") else f"workbench_base: {why}"


async def load_jobs(keys: set) -> dict:
    """(campaign_run, strategy) -> job_body (dict) из backtest_runs; только своя стратегия кампании."""
    import asyncpg
    c = await asyncpg.connect(os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql"))
    try:
        out = {}
        for cr, st in sorted(keys):
            x = await c.fetchrow("select job_body from backtest_runs where id like $1 and strategy = $2 limit 1",
                                 cr + "-r%", st)
            out[(cr, st)] = _j(x["job_body"]) if x else None
        return out
    finally:
        await c.close()


def main() -> None:
    with open(REGISTRY, encoding="utf-8") as f:
        registry = json.load(f)["campaigns"]
    runs, tasks, bf, task_results, tops, meta = asyncio.run(load_db(registry))
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    ctx = sv.BarsCtx(os.path.join(ROOT, "agent_bars"), meta)
    out = build(registry, runs, tasks, bf, task_results, now, ctx, tops)
    keys = {(d["_wb"]["campaign_run"], d["_wb"]["strategy"]) for _, d in out
            if d.get("kind") == "optimizer" and d.get("_wb") and d["_wb"].get("strategy")}
    apply_workbench(out, asyncio.run(load_jobs(keys)), ctx)
    n_wb = sum(1 for _, d in out if d.get("workbench_base"))
    print(f"workbench_base заполнено у {n_wb} из {sum(1 for _, d in out if d.get('kind') == 'optimizer')} optimizer")
    red = build_redirects(out)
    write_all(out, OUT_DIR, red)
    by = {}
    for c, _ in out:
        by[c["status"]] = by.get(c["status"], 0) + 1
    with_syms = sum(1 for c, _ in out if c["symbols"])
    units = {}
    for c, _ in out:
        units[c["unit"]] = units.get(c["unit"], 0) + 1
    print(f"symbols заполнено у {with_syms}, unit: {units}")
    with_curve = sum(1 for c, _ in out if c["thumb"])
    print(f"карточек {len(out)}: {by}; с кривой {with_curve}, без {len(out) - with_curve}; -> {OUT_DIR}")
    full = [ld for _, d in out for ld in d["leaders"] if ld.get("full_cost_rub") is not None]
    bh = [ld for _, d in out for ld in d["leaders"] if ld.get("buyhold_curve")]
    nl = sum(len(d["leaders"]) for _, d in out)
    print(f"лидеров {nl}, с full_cost_rub {len(full)}, с buyhold {len(bh)}, редиректов {len(red)}")
    print(f"прогонов в лидерборде {len(runs)}, заданий agent_tasks {len(tasks)}, "
          f"кампаний с бэкфиллом {len(bf)}")


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    main()
