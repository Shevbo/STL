"""Аудит бумажных роботов: журнал филлов, пересчёт под реальные издержки исполнения.

Локальный расчёт по ВЫГРУЗКАМ (не бэктест, на хостер и i9 не ходит). Вход (--data):
  algo_trades.csv   журнал алготорговли (mode=paper/real), агентские роботы
  live_trades.csv   бумажные филлы роботов STL (LiveRuntime), без комиссии
  robots2.json      таблица robots (имя, params, deployed)
  mirror.json       /api/v1/quik/robots-mirror
  imeta.csv         instrument_meta (шаг цены, стоимость шага, point_value)
  book<КОД>f0929.json   выжимки стакана (Si, GD) для медианного спреда
  --fills-v2        scratchpad/exec/fills_v2.csv (факт исполнения реальных заявок RI)
Выход (--out): inventory.md, paper_fills.csv, paper_report.md.

Бумажная цена (см. docs в отчёте): агент robot_runner/runtime.py:152-156 и STL
trader/lab/runtime.py:338-344 исполняют по цене, которую передала стратегия
(close последнего бара), мгновенно, без спреда/стакана. Комиссия: агент = тейкер
полная (runtime.py:382-391), STL-бумага = ноль.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trader.lab.commission import commission_for  # noqa: E402

MONTH = "FGHJKMNQUVXZ"
MSK = timedelta(hours=3)


def cls_of(q: int) -> str:
    return "1" if q <= 1 else "2-5" if q <= 5 else "6-10" if q <= 10 else "11+"


def load_imeta(path):
    d = pd.read_csv(path)
    out = {}
    for r in d.itertuples():
        pv = r.point_value if pd.notna(r.point_value) else (
            r.step_value / r.price_step if pd.notna(r.price_step) and r.price_step else np.nan)
        if pd.notna(pv):
            out[r.symbol] = (float(pv), float(r.price_step))
    return out


def meta_of(sym, imeta):
    if sym in imeta:
        return imeta[sym], False
    c = [s for s in imeta if s[:2] == sym[:2] and len(s) == 4]
    if not c:
        return (np.nan, np.nan), True
    s = max(c, key=lambda s: (s[3], MONTH.find(s[2])))
    return imeta[s], True


def ri_matrix(fills_v2):
    d = pd.read_csv(fills_v2)
    f0 = d[(d.outcome == "filled") & d.fact_vs_mid.notna() & (d.book_age_ms <= 3000)
           & (d.robot != "smart_order") & d.code.str.startswith("RI")].copy()
    f0["cls"] = f0.qty.map(cls_of)
    f0["tod"] = np.where(f0.hour < 10, "morn", "day")
    out = []
    for f in (f0[f0.taker == True], f0):  # noqa: E712  (маркетабельные; все исполненные)
        g = f.groupby(["cls", "tod"]).fact_vs_mid.agg(["count", "mean", "median"])
        out += [{k: float(v) for k, v in g["mean"].items()}, g, len(f)]
    return out


def book_halfspread(data):
    """Si/GD: половина медианного спреда по книге, утро(<10 МСК)/день."""
    res = {}
    for fam, code in (("Si", "SiZ6"), ("GD", "GDZ6")):
        p = os.path.join(data, f"book{code}f0929.json")
        if not os.path.exists(p):
            continue
        r = np.array(json.load(open(p, encoding="utf-8"))["rows"], dtype=float)
        sp = r[:, 11] - r[:, 1]
        hr = ((r[:, 0] / 1000 // 3600) % 24).astype(int)   # метка уже МСК (time_base в шапке)
        ok = sp > 0
        res[fam] = {"morn": float(np.median(sp[ok & (hr < 10)])) / 2,
                    "day": float(np.median(sp[ok & (hr >= 10)])) / 2}
    return res


def strat_of(sc):
    m = re.findall(r"make_on_bar\(['\"]([^'\"]+)", sc or "")
    if m:
        return m[0]
    m = re.findall(r"strategies\.(\w+) import", sc or "")
    return m[0] if m else "?"


def replay(df):
    """Знаковая проходка (как trader/lab/robot_stand.walk_fills). Возвращает gross пунктов по филлу,
    pos_after, роль."""
    pos, avg = 0, 0.0
    g, pa, role = [], [], []
    for side, q, px in zip(df.side, df.qty, df.price):
        d = q if side == "buy" else -q
        realized = 0.0
        if pos == 0:
            r = "open"
            avg, pos = px, d
        elif (pos > 0) == (d > 0):
            r = "add"
            avg = (avg * abs(pos) + px * q) / (abs(pos) + q)
            pos += d
        else:
            take = min(q, abs(pos))
            realized = (px - avg) * take * (1 if pos > 0 else -1)
            rest = q - take
            r = "close" if (rest == 0 and take == abs(pos)) else ("reduce" if rest == 0 else "flip")
            pos += d
            if rest:
                avg = px
            elif pos == 0:
                avg = 0.0
        g.append(realized)
        pa.append(pos)
        role.append(r)
    return g, pa, role


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--fills-v2", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    D, out_dir = a.data, a.out
    imeta = load_imeta(os.path.join(D, "imeta.csv"))
    ri_m, ri_tab, ri_n, ri_m_lo, ri_tab_lo, ri_n_lo = ri_matrix(a.fills_v2)
    bk = book_halfspread(D)
    robots = {r["id"]: r for r in json.load(open(os.path.join(D, "robots2.json"), encoding="utf-8"))}
    mirror = {r["robot_id"]: r for r in json.load(open(os.path.join(D, "mirror.json"), encoding="utf-8"))["robots"]}

    # ---------- филлы ----------
    al = pd.read_csv(os.path.join(D, "algo_trades.csv"))
    ag = al[al["mode"] == "paper"].copy()
    ag["pop"] = "agent"
    ag["ts"] = ag.ts_ms / 1000.0
    ag["pv"] = ag.point_value
    ag["led_comm"] = ag.commission_rub
    ag["gross_rub"] = ag.pnl_gross_rub
    ag["gross_pts"] = ag.pnl_gross_rub / ag.point_value
    ag = ag.sort_values(["robot_id", "seq"])
    d_ = np.where(ag.side == "buy", ag.qty, -ag.qty)
    pb = ag.pos_after - d_
    ag["role"] = np.select(
        [pb == 0, (pb > 0) == (d_ > 0), (ag.pos_after == 0)],
        ["open", "add", "close"], default="reduce")
    ag["role"] = np.where((pb != 0) & ((pb > 0) != (d_ > 0)) & (np.sign(ag.pos_after) != np.sign(pb)) & (ag.pos_after != 0),
                          "flip", ag.role)
    ag = ag.rename(columns={"robot_id": "robot"})[["robot", "pop", "ts", "symbol", "side", "qty", "price", "pv",
                                                   "led_comm", "gross_rub", "gross_pts", "pos_after", "role"]]

    lt = pd.read_csv(os.path.join(D, "live_trades.csv"))
    lt = lt[lt.status == "paper"].copy()
    lt["ts"] = (pd.to_datetime(lt.ts, utc=True, format="ISO8601")
                - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    lt = lt.sort_values(["robot_id", "symbol", "ts"], kind="stable")
    parts = []
    for (rid, sym), g in lt.groupby(["robot_id", "symbol"], sort=False):
        gp, pa, ro = replay(g)
        (pv, _), _est = meta_of(sym, imeta)
        h = pd.DataFrame({"robot": rid, "pop": "lab", "ts": g.ts.values, "symbol": sym, "side": g.side.values,
                          "qty": g.qty.values, "price": g.price.astype(float).values, "pv": pv,
                          "led_comm": 0.0, "gross_pts": gp, "pos_after": pa, "role": ro})
        h["gross_rub"] = h.gross_pts * pv
        parts.append(h)
    lb = pd.concat(parts, ignore_index=True)
    F = pd.concat([ag, lb], ignore_index=True)
    F = F.sort_values(["robot", "ts"], kind="stable").reset_index(drop=True)

    # ---------- издержки ----------
    msk = pd.to_datetime(F.ts + 10800, unit="s")
    F["hour"] = msk.dt.hour
    F["date"] = msk.dt.date
    F["cls"] = F.qty.map(cls_of)
    F["tod"] = np.where(F.hour < 10, "morn", "day")
    fam = F.symbol.str[:2]
    steps = np.array([meta_of(s, imeta)[0][1] for s in F.symbol])
    half = np.zeros(len(F))
    half_lo = np.zeros(len(F))
    src = []
    for i, (fm, tod, c) in enumerate(zip(fam, F.tod, F.cls)):
        if fm == "RI":
            half[i] = ri_m.get((c, tod), np.nan)
            half_lo[i] = ri_m_lo.get((c, tod), np.nan)
            src.append("RI факт")
        elif fm in bk:
            half[i] = half_lo[i] = bk[fm][tod]
            src.append("книга")
        else:
            half[i] = half_lo[i] = steps[i] / 2
            src.append("оценка")
    F["cost_src"] = src
    F["exec_pts"] = half * F.qty
    F["exec_rub"] = F.exec_pts * F.pv
    F["exec_rub_lo"] = half_lo * F.qty * F.pv
    F["comm_full"] = [commission_for(s, p, q, v, taker=True, ts=t + 10800)
                      for s, p, q, v, t in zip(F.symbol, F.price, F.qty, F.pv, F.ts)]
    F["comm_scalp"] = [commission_for(s, p, q, v, taker=True, scalper=True, ts=t + 10800)
                       for s, p, q, v, t in zip(F.symbol, F.price, F.qty, F.pv, F.ts)]
    F["turn_rub"] = F.price * F.pv * F.qty

    # ---------- таблица по роботам ----------
    rows = []
    for rid, g in F.groupby("robot", sort=False):
        first, last = g.ts.min(), g.ts.max()
        d0 = (pd.Timestamp(first + 10800, unit="s")).date()
        d1 = (pd.Timestamp(last + 10800, unit="s")).date()
        cal = (d1 - d0).days + 1
        act = g.date.nunique()
        lots = int(g.qty.sum())
        circles = int(((g.pos_after == 0) | (g.role == "flip")).sum())
        gross = g.gross_rub.sum()
        paper_net = gross - g.led_comm.sum()
        nrf = gross - g.exec_rub.sum() - g.comm_full.sum()
        nrs = gross - g.exec_rub.sum() - g.comm_scalp.sum()
        rb = robots.get(rid, {})
        mr = mirror.get(rid)
        strat = (mr or {}).get("strategy_id") or (
            strat_of(rb["sc"]) if rb else ("order_block" if rid.startswith("order_block") else "?"))
        rows.append(dict(
            robot=rid, pop=g["pop"].iloc[0], strat=strat, symbols=",".join(sorted(g.symbol.unique())),
            first=str(d0), last=str(d1), cal=cal, act=act, fills=len(g), lots=lots,
            avg_lot=lots / len(g), turn=g.turn_rub.sum(), circles=circles, circ_day=circles / cal,
            gross_pts=g.gross_pts.sum(), gross=gross, paper_comm=g.led_comm.sum(), paper_net=paper_net,
            exec=g.exec_rub.sum(), comm_full=g.comm_full.sum(), comm_scalp=g.comm_scalp.sum(),
            net_full=nrf, net_scalp=nrs, exec_lo=g.exec_rub_lo.sum(),
            net_full_lo=gross - g.exec_rub_lo.sum() - g.comm_full.sum(),
            net_scalp_lo=gross - g.exec_rub_lo.sum() - g.comm_scalp.sum(),
            circ_act=circles / act, est_share=float((g.cost_src == "оценка").mean()),
            gross_pt_lot=g.gross_pts.sum() / lots, exec_pt_lot=g.exec_pts.sum() / lots,
            comm_pt_lot=(g.comm_full / g.pv).sum() / lots,
            open_pos=int(g.pos_after.iloc[-1]),
        ))
    R = pd.DataFrame(rows).sort_values(["pop", "robot"]).reset_index(drop=True)
    R.to_csv(os.path.join(out_dir, "paper_by_robot.csv"), index=False)

    out = F.copy()
    out["ts_msk"] = pd.to_datetime(out.ts + 10800, unit="s").dt.strftime("%Y-%m-%d %H:%M:%S")
    out[["robot", "pop", "ts_msk", "side", "qty", "price", "symbol", "role", "pos_after", "gross_pts",
         "exec_pts", "comm_full", "comm_scalp"]].round(4).to_csv(os.path.join(out_dir, "paper_fills.csv"), index=False)

    # ---------- распределения ----------
    hb = pd.cut(F.hour, [-1, 6, 9, 12, 15, 18, 23], labels=["00-06", "07-09", "10-12", "13-15", "16-18", "19-23"])
    H = pd.crosstab(F.robot, hb)
    H = (H.div(H.sum(1), axis=0) * 100).round(0).astype(int)
    S = pd.crosstab(F.robot, F.cls)[["1", "2-5", "6-10", "11+"]]
    S = (S.div(S.sum(1), axis=0) * 100).round(0).astype(int)

    # ---------- двойник lxk22 ----------
    tw = ""
    pp = F[F.robot == "paper-lxk22-cost40"]
    rl = al[(al.robot_id == "lxk22tsffsxiiotb8kmpsato") & (al["mode"] == "real")].copy()
    if len(pp) and len(rl):
        t0, t1 = pp.ts.min(), pp.ts.max()
        rl["ts"] = rl.ts_ms / 1000.0
        rl = rl[(rl.ts >= t0) & (rl.ts <= t1)]
        rl["gp"] = rl.pnl_gross_rub / rl.point_value

        def line(name, n, lots, gp, gr, days, comm):
            return (f"| {name} | {n} | {lots} | {lots / n:.2f} | {n / days:.1f} | {gp:.0f} | {gp / lots:.2f} | "
                    f"{gp / n:.2f} | {gr:.0f} | {comm:.0f} |")
        days = max(1, (pd.Timestamp(t1 + 10800, unit="s").date() - pd.Timestamp(t0 + 10800, unit="s").date()).days + 1)
        tw = ["| ряд | филлов | лотов | лотов/филл | филлов/день | gross пт | gross пт/лот | gross пт/филл | gross ₽ | комиссия ₽ |",
              "|---|---|---|---|---|---|---|---|---|---|",
              line("paper-lxk22-cost40 (бумага)", len(pp), int(pp.qty.sum()), pp.gross_pts.sum(), pp.gross_rub.sum(), days, pp.led_comm.sum()),
              line("lxk22 реальный, то же окно", len(rl), int(rl.qty.sum()), rl.gp.sum(), rl.pnl_gross_rub.sum(), days, rl.commission_rub.sum())]
        tw = "\n".join(tw) + f"\n\nОкно {pd.Timestamp(t0 + 10800, unit='s')} .. {pd.Timestamp(t1 + 10800, unit='s')} МСК, {days} кал. дн."
        pgr = pp.gross_pts.sum() / pp.qty.sum()
        tw += (f"\nМодель издержек исполнения на бумажных филлах: {pp.exec_pts.sum() / pp.qty.sum():.2f} пт/лот; "
               f"бумажный gross за вычетом модели: {pgr - pp.exec_pts.sum() / pp.qty.sum():.2f} пт/лот.")

    # ---------- запись отчёта ----------
    def md(df, cols, fmt):
        h = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
        return h + "\n".join("| " + " | ".join(fmt.get(c, str)(r[c]) for c in cols) + " |" for _, r in df.iterrows())

    f0 = lambda x: f"{x:,.0f}".replace(",", " ")  # noqa: E731
    f1 = lambda x: f"{x:.1f}"  # noqa: E731
    f2 = lambda x: f"{x:.2f}"  # noqa: E731
    R["net_paper_day"] = R.paper_net / R.cal
    R["net_full_day"] = R.net_full / R.cal
    R["net_scalp_day"] = R.net_scalp / R.cal
    R["gross_day"] = R.gross / R.cal
    R["pop_s"] = R["pop"].map({"agent": "агент", "lab": "STL"})
    R["est_s"] = (R.est_share * 100).round(0).astype(int).astype(str) + "%"

    rep = []
    rep.append("# Бумажные роботы: журнал и пересчёт под реальные издержки\n")
    rep.append("Ключ: агент = бумага на раннере QUIK-агента (журнал algo_trades mode=paper); STL = бумага LiveRuntime "
               "в процессе STL (live_trades status=paper). Деньги в ₽, пункты = пункты цены. Gross = реализованный "
               "(закрытые части позиции), нереализованный открытой позиции не входит; издержки считаются по ВСЕМ филлам, "
               "включая открывающие, поэтому у робота с открытой позицией на конец net занижен.\n")
    rep.append("## Мерка исполнения (RI, реальные заявки роботов, fills_v2: filled, снимок ≤3 с, без smart_order)\n")
    ren = {"cls": "размер", "tod": "время", "count": "n", "mean": "среднее", "median": "медиана"}
    rep.append(f"Основная (верх издержек): только МАРКЕТАБЕЛЬНЫЕ заявки (taker), строк {ri_n}; пассивные заявки с \"отрицательной\" "
               "издержкой (дрейф к нам за ожидание) сюда не входят, потому что бумага всегда исполняется мгновенно. Пт на ЛОТ, к mid.\n")
    rep.append(md(ri_tab.reset_index().rename(columns=ren), ["размер", "время", "n", "среднее", "медиана"],
                  {"среднее": f2, "медиана": f2}))
    rep.append(f"\nНижняя (колонки _lo): ВСЕ исполненные заявки роботов, строк {ri_n_lo}, включая пассивные.\n")
    rep.append(md(ri_tab_lo.reset_index().rename(columns=ren), ["размер", "время", "n", "среднее", "медиана"],
                  {"среднее": f2, "медиана": f2}))
    rep.append("\nНе-RI: половина медианного спреда книги (Si: утро %.2f / день %.2f пт; GD: %.2f пт), остальное = шаг цены/2 "
               "(оценка, нижняя граница). Размер заявки и утро для не-RI не различались.\n" % (
                   bk.get("Si", {}).get("morn", np.nan), bk.get("Si", {}).get("day", np.nan), bk.get("GD", {}).get("day", np.nan)))
    rep.append("\n## Таблица 1. Жизнь, обороты, бумажный и реальный net\n")
    c1 = ["robot", "pop_s", "strat", "symbols", "first", "last", "cal", "act", "fills", "lots", "circles", "circ_day", "circ_act"]
    rep.append("cal = календарных дней жизни (первый..последний филл), act = дней с филлами, circ_day/circ_act = кругов "
               "(возврат в ноль или переворот) на cal/act.\n")
    rep.append(md(R, c1, {"circ_day": f1, "circ_act": f1}))
    rep.append("\n## Таблица 2. Деньги (₽ за всю жизнь)\n")
    rep.append("paper net = то, что показывает бумага (агент: gross минус тейкерская комиссия журнала; STL: gross, комиссии нет). "
               "Издержки исполнения = мерка выше. Две комиссии: полная тейкер (с выходными x2) и со скальперской скидкой 0.5 на биржевую часть всех филлов (верхняя граница скидки). "
               "net реальный = gross - исполнение - комиссия.\n")
    c2 = ["robot", "gross", "paper_comm", "paper_net", "exec", "comm_full", "comm_scalp", "net_full", "net_scalp", "est_s"]
    rep.append(md(R, c2, {k: f0 for k in c2[1:9]}))
    rep.append("\n### Таблица 2b. То же с нижней мерой исполнения (все исполненные заявки роботов)\n")
    c2b = ["robot", "gross", "paper_net", "exec_lo", "net_full_lo", "net_scalp_lo"]
    rep.append(md(R, c2b, {k: f0 for k in c2b[1:]}))
    rep.append("\n## Таблица 3. ₽ в день (на календарный день жизни) и пункты на лот\n")
    c3 = ["robot", "cal", "gross_day", "net_paper_day", "net_full_day", "net_scalp_day", "gross_pt_lot", "exec_pt_lot", "comm_pt_lot", "open_pos"]
    rep.append(md(R, c3, {"gross_day": f0, "net_paper_day": f0, "net_full_day": f0, "net_scalp_day": f0,
                          "gross_pt_lot": f2, "exec_pt_lot": f2, "comm_pt_lot": f2}))
    rep.append("\ncomm_pt_lot считается по полной тейкерской комиссии. open_pos = позиция в контрактах после последнего филла.\n")
    rep.append("\n## Таблица 4. Распределение филлов по часу МСК и размеру заявки (% филлов)\n")
    HS = H.join(S, rsuffix="_sz")
    HS.columns = [f"ч {c}" for c in H.columns] + [f"{c} лот" for c in S.columns]
    HS = HS.reset_index()
    rep.append(md(HS, list(HS.columns), {}))
    rep.append("\n## Таблица 5. Двойник: lxk22 реальный против paper-lxk22-cost40\n")
    rep.append(tw or "нет данных")

    # ---------- прямое измерение: цена бумажного филла против рынка (RIZ6) ----------
    bk_path = os.path.join(D, "bookRIZ6f0929.json")
    bars_path = os.path.join(D, "RIZ6.json")
    rep.append("\n## Таблица 7. Бумажная цена против живого стакана и баров, RIZ6 (прямое измерение)\n")
    if os.path.exists(bk_path) and os.path.exists(bars_path):
        r = np.array(json.load(open(bk_path, encoding="utf-8"))["rows"], dtype=float)
        bts, bbid, bask = r[:, 0] / 1000, r[:, 1], r[:, 11]
        X = F[F.symbol == "RIZ6"].copy()
        X["t"] = X.ts + 10800
        X = X[(X.t >= bts[0]) & (X.t <= bts[-1])]
        i = np.searchsorted(bts, X.t.values, side="right") - 1
        X = X[(X.t.values - bts[i]) <= 3].copy()
        i = np.searchsorted(bts, X.t.values, side="right") - 1
        buy = (X.side == "buy").values
        X["to_touch"] = np.where(buy, bask[i] - X.price.values, X.price.values - bbid[i])
        X["to_mid"] = np.where(buy, (bbid[i] + bask[i]) / 2 - X.price.values, X.price.values - (bbid[i] + bask[i]) / 2)
        rows7 = []
        for pop, g in X.groupby("pop"):
            for cl, gg in list(g.groupby("cls")) + [("все", g)]:
                rows7.append(dict(pop=pop, cls=cl, n=len(gg), to_touch_mean=gg.to_touch.mean(), to_touch_med=gg.to_touch.median(),
                                  lotw=(gg.to_touch * gg.qty).sum() / gg.qty.sum(), mid_mean=gg.to_mid.mean(), mid_med=gg.to_mid.median()))
        T7 = pd.DataFrame(rows7)
        rep.append("Окно: %s .. %s МСК (стакан RIZ6 f0929), снимок не старше 3 с. to_touch = на сколько встречный best (покупка: ask, продажа: bid) "
                   "хуже бумажной цены, пт (>0: реальная маркетабельная заявка дороже бумаги); to_mid = (mid - цена) для покупки, (цена - mid) для продажи "
                   "(>0: бумага лучше mid, <0: бумага платит выше mid).\n" % (
                       pd.Timestamp(bts[0], unit="s"), pd.Timestamp(bts[-1], unit="s")))
        rep.append(md(T7, ["pop", "cls", "n", "to_touch_mean", "to_touch_med", "lotw", "mid_mean", "mid_med"],
                      {k: f2 for k in ("to_touch_mean", "to_touch_med", "lotw", "mid_mean", "mid_med")}))
        # чьё закрытие бара = цена бумажного филла
        bb = pd.DataFrame(json.load(open(bars_path, encoding="utf-8"))["rows"], columns=["t", "o", "h", "l", "c", "v"]).set_index("t")
        Y = F[F.symbol == "RIZ6"].copy()
        Y["m"] = ((Y.ts + 10800) // 60 * 60).astype(int)
        Y = Y[(Y.m >= bb.index.min()) & (Y.m <= bb.index.max())]
        rep.append("\nКакому бару равна цена бумажного филла (доля филлов, у которых цена == close бара со сдвигом k минут от минуты филла; k=-1 это только что закрывшийся бар):\n")
        rows8 = []
        for pop, g in Y.groupby("pop"):
            row = dict(pop=pop, n=len(g))
            for k in (-3, -2, -1, 0, 1):
                key = g.m + 60 * k
                ok = key.isin(bb.index)
                row[f"k={k}"] = float((np.abs(g.price[ok].values - bb.loc[key[ok], "c"].values) < 1e-9).mean())
            rows8.append(row)
        rep.append(md(pd.DataFrame(rows8), ["pop", "n"] + [f"k={k}" for k in (-3, -2, -1, 0, 1)], {f"k={k}": f2 for k in (-3, -2, -1, 0, 1)}))
    else:
        rep.append("нет файлов стакана/баров RIZ6")

    # ---------- сверка леджера с раннером ----------
    rec = ["| робот | зеркало realized_pnl (пт, net раннера) | леджер paper net (пт) | леджер paper gross (пт) | леджер net (₽) |", "|---|---|---|---|---|"]
    for rid, r in mirror.items():
        L = al[(al.robot_id == rid) & (al["mode"] == "paper")]
        if len(L):
            rec.append(f"| {rid} | {r['realized_pnl']:.0f} | {(L.pnl_net_rub / L.point_value).sum():.0f} | "
                       f"{(L.pnl_gross_rub / L.point_value).sum():.0f} | {L.pnl_net_rub.sum():.0f} |")
    rep.append("\n## Таблица 6. Сверка: realized раннера (зеркало) и леджер paper\n")
    rep.append("\n".join(rec))

    # ---------- inventory.md ----------
    keyp = ("qty", "avg_max", "k_avg", "tp_atr", "sl_pct", "sl_frac", "tod_m1", "tod_m2", "tod_s1", "tod_s2", "tod_s3",
            "min_gap_pts", "cooldown_min", "allow_long", "allow_short", "exit_only", "cost_atr", "bet_step", "super_y", "bet_max")
    Rx = R.set_index("robot")
    inv = ["# Инвентарь бумажных роботов (снимок %s)\n" % pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
           "Источники: robots-mirror агента, таблица robots, algo_trades, live_trades. Дата запуска: lab = deployed_at "
           "(если пусто, первый филл), агент = первый paper-филл леджера.\n",
           "## A. Агентская бумага и реальные роботы агента (зеркало)\n",
           "| id | стратегия | инструмент | режим | работает | max_position | qty | запуск (1й paper-филл) | филлов в леджере paper | последний филл | ключевые params |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for rid, r in mirror.items():
        pr = json.loads(r["params_json"])
        kp = ", ".join(f"{k}={pr[k]}" for k in keyp if k in pr and k != "qty")
        row = Rx.loc[rid] if rid in Rx.index else None
        inv.append(f"| {rid} | {r['strategy_id']} | {r['symbol']} | {'paper' if r.get('paper') else 'REAL'} | "
                   f"{'да' if r['running'] else 'нет'} | {r['max_position']} | {pr.get('qty', '')} | "
                   f"{row['first'] if row is not None else '-'} | {int(row['fills']) if row is not None else 0} | "
                   f"{row['last'] if row is not None else '-'} | {kp}; расписание {r['schedule']} |")
    led_only = [r for r in R.robot if r not in mirror and R.set_index('robot').loc[r, 'pop'] == 'agent']
    for rid in led_only:
        row = Rx.loc[rid]
        inv.append(f"| {rid} | {row.strat} | {row.symbols} | paper (нет в зеркале) | нет | - | - | {row['first']} | {int(row.fills)} | {row['last']} | прежний id order_block BRU6 |")
    inv += ["\n## B. Бумага STL (LiveRuntime в процессе STL): deployed\n",
            "| id | имя | стратегия | инструмент (params) | запуск | обновлён | расписание | филлов | первый | последний | ключевые params |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    def lab_rows(dep):
        out = []
        for rid, rb in sorted(robots.items(), key=lambda kv: kv[0]):
            if bool(rb["deployed"]) != dep or rid not in Rx.index or Rx.loc[rid, "pop"] != "lab":
                continue
            pr = json.loads(rb["p"]) if isinstance(rb["p"], str) else rb["p"]
            kp = ", ".join(f"{k}={pr[k]}" for k in keyp if k in pr)
            row = Rx.loc[rid]
            out.append(f"| {rid} | {rb['name']} | {strat_of(rb['sc'])} | {pr.get('symbol', row.symbols)} | "
                       f"{rb['deployed_at'][:10] if rb['deployed_at'] != 'None' else row['first']} | {rb['updated_at'][:10]} | "
                       f"{rb['schedule']} | {int(row.fills)} | {row['first']} | {row['last']} | {kp} |")
        return out
    inv += lab_rows(True)
    inv += ["\n## C. Бумага STL: не deployed (остановлены), есть филлы\n",
            "| id | имя | стратегия | инструмент (params) | запуск | обновлён | расписание | филлов | первый | последний | ключевые params |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    inv += lab_rows(False)
    open(os.path.join(out_dir, "inventory.md"), "w", encoding="utf-8").write("\n".join(inv) + "\n")
    open(os.path.join(out_dir, "paper_report.md"), "w", encoding="utf-8").write("\n".join(rep) + "\n")
    print(R[["robot", "pop", "fills", "gross", "paper_net", "net_full", "net_scalp"]].round(0).to_string())


if __name__ == "__main__":
    main()
