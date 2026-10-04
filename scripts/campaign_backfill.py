#!/usr/bin/env python3
"""Бэкфилл кривых лидеров кампаний для витрины: перепрогон строки на i9 и сохранение как
`<campaign_run>-bf<rank>` в backtest_results (trades + equity_curve). Рецепт: reference_campaign_backfill.

Считает ТОЛЬКО i9: задание уходит в очередь через POST /api/v1/backtest/run (engine=remote), сам
скрипт ничего не считает. Запускать на хостере (нужны LAB_DB_URL и OPT_AGENT_TOKEN из окружения):

    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PY=$(ls -d ~/.cache/pypoetry/virtualenvs/shectory-trader-*/bin/python | head -1)
    PYTHONPATH=. $PY scripts/campaign_backfill.py select --n 20 --out /tmp/bf_sel.json
    PYTHONPATH=. $PY scripts/campaign_backfill.py queue  --sel /tmp/bf_sel.json [--only <campaign_run>-bf0]
    PYTHONPATH=. $PY scripts/campaign_backfill.py store  --sel /tmp/bf_sel.json   # забрать готовые

Правила выбора (select): карточки optimizer без кривой, не служебные (gate/plc/exec), лучшая строка с
net>0 и >=30 сделок; среди 60 сильнейших по score берём 20 самых свежих по created_at; лидеры rank 1-3
= три лучшие по net строки (без дублей параметров) того же инструмента и стратегии в лучшем прогоне.
Один campaign_run - одна карточка: имена bf0..bf2 не пересекаются. Нужен job_body кампании в
backtest_runs (скрипт и окно оригинала): перепрогон по шаблону стратегии и окну из лидерборда строку
НЕ воспроизвёл (проверено на opt-*), такие карточки пропускаются.

Сохранённый net = net ПЕРЕПРОГОНА (честный); net строки лидерборда лежит в extra.lb_net, расхождение
печатает `store`. Не считать совпавшим молча: допуск 1%, иначе пометка MISMATCH.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from scripts import campaign_showcase_build as sb  # noqa: E402

SERVICE = re.compile(r"^camp-\d{8}-(gate|plc|exec)")
TOL = 0.01


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _db_url() -> str:
    return os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql")


def pick(cards: list, scores: dict, n: int | None = 20, pool: int = 60) -> list:
    """Чистое правило отбора. cards: заготовки merge_cards; scores: slug -> score лучшей строки."""
    cand, used = [], set()
    for c in cards:
        e = c["entry"]
        if not c["auto"] or e.get("kind") != "optimizer" or not c["runs"]:
            continue
        best = max(c["runs"], key=lambda r: r.get("net") if r.get("net") is not None else -1e18)
        if SERVICE.match(best["campaign_run"]) or (best.get("net") or 0) <= 0 or (best.get("trades") or 0) < 30:
            continue
        if scores.get(e["slug"]) is None:
            continue
        cand.append((e["slug"], best, scores[e["slug"]]))
    cand.sort(key=lambda t: -t[2])
    cand = sorted(cand[:pool], key=lambda t: t[1].get("created_at") or "", reverse=True)
    out = []
    for slug, best, _ in cand:
        if best["campaign_run"] in used:
            continue
        used.add(best["campaign_run"])
        out.append((slug, best))
        if n and len(out) == n:
            break
    return out


async def cmd_select(a) -> None:
    import asyncpg
    c = await asyncpg.connect(_db_url())
    try:
        reg = json.load(open(sb.REGISTRY, encoding="utf-8"))["campaigns"]
        rows = await c.fetch("""
            select distinct on (campaign_run, strategy, symbol)
                   campaign_run, net_profit, total_trades, max_drawdown, strategy,
                   symbol, params, point_value, date_from, date_to, created_at, score
            from optimization_leaderboard
            order by campaign_run, strategy, symbol, net_profit desc nulls last""")
        runs = [{"campaign_run": x["campaign_run"], "net": x["net_profit"], "trades": x["total_trades"],
                 "max_dd": x["max_drawdown"], "strategy": x["strategy"], "symbol": x["symbol"],
                 "params": _j(x["params"]), "point_value": x["point_value"],
                 "date_from": str(x["date_from"]) if x["date_from"] else None,
                 "date_to": str(x["date_to"]) if x["date_to"] else None,
                 "created_at": x["created_at"].isoformat() if x["created_at"] else None,
                 "score": x["score"]} for x in rows]
        have = {r["run_id"].rsplit("-bf", 1)[0] for r in await c.fetch(
            "select run_id from backtest_results where run_id like '%-bf_'")}
        cards = [cd for cd in sb.merge_cards(reg["campaigns"] if isinstance(reg, dict) else reg, runs)
                 if not any(r["campaign_run"] in have for r in cd["runs"])]
        scores = {}
        for cd in cards:
            if cd["auto"] and cd["runs"]:
                best = max(cd["runs"], key=lambda r: r.get("net") if r.get("net") is not None else -1e18)
                scores[cd["entry"]["slug"]] = best.get("score")
        sel = []
        for slug, best in pick(cards, scores, None):
            if len(sel) == a.n:
                break
            cr = best["campaign_run"]
            # у многостратегийной кампании (до 16) job_body у каждой стратегии свой: берём ТОЛЬКО свой,
            # иначе чужой скрипт молча считает чужую стратегию (так бэкфилл и записал order_block вместо macd_cross)
            jb = await c.fetchrow("""select id, robot_id, job_body from backtest_runs
                where id like $1 and strategy = $2 limit 1""", cr + "-r%", best["strategy"])
            body = _j(jb["job_body"]) if jb else None
            if not body or not body.get("scriptCode"):
                # шаблон стратегии + окно строки пробовали (opt-*): перепрогон не воспроизводит
                # строку (46 против 148 сделок, знак net другой), поэтому без job_body не считаем
                print(f"пропуск {slug}: у {cr} нет job_body стратегии {best['strategy']} со scriptCode")
                continue
            lead = await c.fetch("""select params, net_profit, total_trades from optimization_leaderboard
                where campaign_run=$1 and strategy=$2 and symbol=$3 order by net_profit desc nulls last
                limit 40""", cr, best["strategy"], best["symbol"])
            seen, leaders = set(), []
            for x in lead:
                p = _j(x["params"])
                # те же net и сделки = те же сделки (ненужные оси): второй раз кривая не нужна
                k = json.dumps([round(x["net_profit"] or 0, 2), x["total_trades"]])
                if k in seen:
                    continue
                seen.add(k)
                leaders.append({"rank": len(leaders), "params": p, "lb_net": x["net_profit"],
                                "lb_trades": x["total_trades"]})
                if len(leaders) == 3:
                    break
            sel.append({"slug": slug, "campaign_run": cr, "strategy": best["strategy"], "symbol": best["symbol"],
                        "robot_id": jb["robot_id"], "script_code": body["scriptCode"],
                        "date_from": body["dateFrom"], "date_to": body["dateTo"], "leaders": leaders})
    finally:
        await c.close()
    json.dump(sel, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for s in sel:
        print(s["slug"], s["campaign_run"], [round(x["lb_net"]) for x in s["leaders"]])
    print(f"выбрано {len(sel)} карточек -> {a.out}")


def job_of(item: dict, ld: dict) -> dict:
    """Задание перепрогона одной строки: те же скрипт, окно и параметры, одна комбинация."""
    return {"scriptCode": item["script_code"], "baseParams": ld["params"], "paramSets": [{}],
            "symbol": item["symbol"], "dateFrom": item["date_from"], "dateTo": item["date_to"],
            "engine": "remote", **({"robotId": item["robot_id"]} if item.get("robot_id") else {})}


def bf_name(item: dict, ld: dict) -> str:
    return f"{item['campaign_run']}-bf{ld['rank']}"


def net_matches(lb: float | None, rerun: float | None, tol: float = TOL) -> bool:
    if lb is None or rerun is None:
        return False
    return abs(lb - rerun) <= tol * max(abs(lb), 1.0)


def cmd_queue(a) -> None:
    import httpx
    sel = json.load(open(a.sel, encoding="utf-8"))
    tok = os.environ.get("OPT_AGENT_TOKEN")
    if not tok:
        sys.exit("нет OPT_AGENT_TOKEN в окружении")
    with httpx.Client(base_url=a.api, headers={"X-Agent-Token": tok}, timeout=60) as cl:
        for it in sel:
            for ld in it["leaders"]:
                name = bf_name(it, ld)
                if (a.only and name not in a.only) or ld.get("run_id"):
                    continue
                r = cl.post("/api/v1/backtest/run", json=job_of(it, ld))
                r.raise_for_status()
                ld["run_id"] = r.json()["run_id"]
                print("в очереди", name, ld["run_id"])
    json.dump(sel, open(a.sel, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


async def cmd_store(a) -> None:
    import asyncpg
    sel = json.load(open(a.sel, encoding="utf-8"))
    c = await asyncpg.connect(_db_url())
    try:
        for it in sel:
            for ld in it["leaders"]:
                if not ld.get("run_id") or ld.get("stored"):
                    continue
                name = bf_name(it, ld)
                st = await c.fetchrow("select status, error_msg from backtest_runs where id=$1", ld["run_id"])
                if not st or st["status"] != "done":
                    print("ждёт", name, st["status"] if st else "?", (st["error_msg"] or "")[:80] if st else "")
                    continue
                res = await c.fetch("select * from backtest_results where run_id=$1", ld["run_id"])
                if len(res) != 1 or not res[0]["trades"]:
                    print("НЕТ РЕЗУЛЬТАТА", name, len(res))
                    continue
                r = dict(res[0])
                ok = net_matches(ld["lb_net"], r["net_profit"])
                old_extra = _j(r.get("extra"))
                extra = {**(old_extra if isinstance(old_extra, dict) else {}), "lb_net": ld["lb_net"], "lb_trades": ld["lb_trades"],
                         "rerun_of": ld["run_id"]}
                par = await c.fetchrow("select * from backtest_runs where id=$1", ld["run_id"])
                if par["strategy"] != it["strategy"]:
                    print("ЧУЖАЯ СТРАТЕГИЯ, не сохраняю", name, par["strategy"], "вместо", it["strategy"])
                    continue
                async with c.transaction():
                    await c.execute("delete from backtest_results where run_id=$1", name)
                    await c.execute("delete from backtest_runs where id=$1", name)
                    await c.execute("""insert into backtest_runs (id, robot_id, params_grid, date_from, date_to,
                        status, engine, symbol, job_body, priority, strategy, finished_at)
                        values ($1,$2,$3,$4,$5,'done',$6,$7,$8,0,$9,$10)""",
                                    name, par["robot_id"], json.dumps(_j(par["params_grid"])), par["date_from"], par["date_to"],
                                    par["engine"], par["symbol"], json.dumps(_j(par["job_body"])), par["strategy"] or it["strategy"],
                                    par["finished_at"])
                    await c.execute("""insert into backtest_results (id, run_id, params, trades, equity_curve,
                        sharpe, max_drawdown, win_rate, total_return, total_trades, net_profit, point_value,
                        recovery_factor, peak_contracts, extra)
                        values ($1,$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",
                                    name, json.dumps(_j(r["params"])), json.dumps(_j(r["trades"])), json.dumps(_j(r["equity_curve"])), r["sharpe"],
                                    r["max_drawdown"], r["win_rate"], r["total_return"], r["total_trades"],
                                    r["net_profit"], r["point_value"], r["recovery_factor"], r["peak_contracts"],
                                    json.dumps(extra))
                ld["stored"] = True
                ld["rerun_net"] = r["net_profit"]
                print(("OK      " if ok else "MISMATCH"), name, "лидерборд", round(ld["lb_net"]),
                      "перепрогон", round(r["net_profit"] or 0), "сделок", ld["lb_trades"], "->", r["total_trades"])
    finally:
        await c.close()
    json.dump(sel, open(a.sel, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("select")
    s.add_argument("--n", type=int, default=20)
    s.add_argument("--out", required=True)
    q = sp.add_parser("queue")
    q.add_argument("--sel", required=True)
    q.add_argument("--only", action="append", help="имя bf целиком, можно несколько")
    q.add_argument("--api", default=os.environ.get("STL_API", "http://127.0.0.1:8000"))
    t = sp.add_parser("store")
    t.add_argument("--sel", required=True)
    a = ap.parse_args()
    if a.cmd == "queue":
        cmd_queue(a)
    else:
        asyncio.run(cmd_select(a) if a.cmd == "select" else cmd_store(a))


if __name__ == "__main__":
    main()
