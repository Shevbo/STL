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
REGISTRY = os.path.join(ROOT, "docs", "campaigns", "registry.json")
OUT_DIR = os.path.join(ROOT, "data", "campaign_showcase")
THUMB_N, CURVE_N, BF_MAX = 200, 1500, 10
LEADERS_N = 8
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
                    name, first = _clip(first, 60) or base, None
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


def build_card(c: dict, tasks_all: list, bf: dict, task_results: dict, now_iso: str):
    """-> (index_card, detail). bf: run -> [rows]; task_results: task_id -> result."""
    e = c["entry"]
    runs = c["runs"]
    tasks = [t for t in tasks_all
             if match_any(t["id"], e.get("task_ids")) or match_any(t["module"], e.get("task_modules"))]
    leaders, notes, reason = [], [], None
    unit = e.get("unit") or "rub"
    if runs:  # единицы берём из реальных строк, реестр - только когда прогонов нет
        best = max(runs, key=lambda r: r.get("net") if r.get("net") is not None else -1e18)
        unit = run_unit(best.get("point_value"))
        mix = sorted({run_unit(r.get("point_value")) for r in runs})
        if len(mix) > 1:
            notes.append(f"в кампании разные единицы ({', '.join(mix)}); карточка по лучшему прогону: {unit}")

    # (1) бэктест-кривые лидеров оптимизатора: <campaign_run>-bf<rank>
    names = sorted({r["campaign_run"] for r in runs})
    rows = [r for n in names for r in bf.get(n, []) if bf_belongs(r, runs)]
    rows = sorted((r for r in rows if r.get("curve")), key=lambda r: -(r.get("net") or 0))[:LEADERS_N]
    for i, r in enumerate(rows):
        leaders.append({"rank": i + 1, "params": r.get("params"), "metrics": {
            "net": r.get("net"), "trades": r.get("trades"), "max_dd_db": r.get("max_dd"),
            "sharpe": r.get("sharpe"), "campaign_run": r["run_id"]},
            "curve": downsample(r["curve"], CURVE_N), "trades_n": r.get("trades")})
    # (2) исследовательские кривые из результатов i9
    spec = e.get("curve")
    if not leaders and spec and task_results:
        res = {t["id"]: task_results[t["id"]] for t in tasks if t["id"] in task_results}
        ls = curve_sweep(res) if spec["kind"] == "sweep" else curve_nextday(res, spec["config"])
        for i, ld in enumerate(ls[:LEADERS_N * 2]):
            ld.pop("_net", None)
            ld["curve"] = downsample(ld["curve"], CURVE_N)
            leaders.append({"rank": i + 1, **ld})
        if spec.get("note"):
            notes.append(spec["note"])
    has_curve = bool(leaders and leaders[0].get("curve"))
    # без кривой: лидеры из лидерборда (метрики без кривой)
    if not has_curve and runs:
        top = sorted(runs, key=lambda r: -(r.get("net") if r.get("net") is not None else -1e18))[:3]
        leaders = [{"rank": i + 1, "params": r.get("params"), "metrics": {
            "net": r.get("net"), "trades": r.get("trades"), "max_dd_db": r.get("max_dd"),
            "campaign_run": r["campaign_run"]}, "curve": None, "trades_n": r.get("trades")}
            for i, r in enumerate(top)]
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
    thumb = downsample(leaders[0]["curve"], THUMB_N) if has_curve else None
    lead_curve = leaders[0]["curve"] if has_curve else None
    top_run = max(runs, key=lambda r: r.get("net") if r.get("net") is not None else -1e18) if runs else None
    if has_curve:
        net = lead_curve[-1][1]
        trades = leaders[0].get("trades_n")
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
        "headline": {"net": net, "trades": trades, "max_dd": dd, "window": window},
        "thumb": thumb, "verdict": e.get("verdict"), "doc": e.get("doc"),
        "unit": unit, "kind": e.get("kind") or "research", "no_curve_reason": reason,
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
        "notes": "; ".join(notes) or e.get("notes"), "auto": c["auto"],
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


def write_all(cards_details: list, out_dir: str) -> None:
    for _, d in cards_details:
        write_atomic(os.path.join(out_dir, f"{d['slug']}.json"), d)
    write_atomic(os.path.join(out_dir, "index.json"), [c for c, _ in cards_details])
    keep = {f"{d['slug']}.json" for _, d in cards_details} | {"index.json"}
    for f in os.listdir(out_dir):  # убрать карточки, исчезнувшие из реестра
        if f.endswith(".json") and f not in keep:
            os.unlink(os.path.join(out_dir, f))


def build(registry: list, runs: list, tasks: list, bf: dict, task_results: dict, now_iso: str) -> list:
    out = [build_card(c, tasks, bf, task_results, now_iso) for c in merge_cards(registry, runs)]
    attach_revisions([d for _, d in out])
    return sort_cards(out)


# ---------- БД ----------

def _j(v):
    return json.loads(v) if isinstance(v, str) else v


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
                   b.strategy
            from backtest_results r left join backtest_runs b on b.id = r.run_id
            where r.run_id = any($1::text[]) and jsonb_array_length(r.equity_curve) > 0""", ids)
        bf: dict = {}
        for x in sorted(have, key=lambda x: -(x["net_profit"] or 0)):
            run = x["run_id"].rsplit("-bf", 1)[0]
            bf.setdefault(run, []).append(dict(run_id=x["run_id"], net=x["net_profit"],
                                               trades=x["total_trades"], max_dd=x["max_drawdown"],
                                               sharpe=x["sharpe"], strategy=x["strategy"]))
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
                    x = await c.fetchrow("select equity_curve from backtest_results where run_id=$1",
                                         r["run_id"])
                    loaded[r["run_id"]] = equity_to_curve(_j(x["equity_curve"]))
                r["curve"] = loaded[r["run_id"]]
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
        return runs, tasks, bf, task_results
    finally:
        await c.close()


def main() -> None:
    with open(REGISTRY, encoding="utf-8") as f:
        registry = json.load(f)["campaigns"]
    runs, tasks, bf, task_results = asyncio.run(load_db(registry))
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out = build(registry, runs, tasks, bf, task_results, now)
    write_all(out, OUT_DIR)
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
    print(f"прогонов в лидерборде {len(runs)}, заданий agent_tasks {len(tasks)}, "
          f"кампаний с бэкфиллом {len(bf)}")


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    main()
