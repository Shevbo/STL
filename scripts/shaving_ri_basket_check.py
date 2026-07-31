#!/usr/bin/env python3
"""Сверка: ручная корзина «Σ вес × цена» против готового индекса RTSI.

Shaving RI 1 берёт опорной серией индекс RTSI, а не сумму по 40+ бумагам. Это
утверждение надо ПРОВЕРИТЬ, а не заявить, поэтому скрипт собирает корзину руками
по опубликованным МосБиржей весам и сравнивает её с RTSI по дневным доходностям.

Заодно показывает вторую причину не считать «RI − GZ·k1 − LK·k2» в рублёвых
ценах: RTSI (и, значит, фьючерс RI) долларовый, а акции рублёвые, поэтому
рублёвая корзина расходится с индексом ровно на движение USD/RUB.

Запускать на хостере (ISS с дев-машины недоступен):
    ssh hoster 'cd ~/apps/shectory-trader && poetry run python \
        scripts/shaving_ri_basket_check.py --date-from 2026-05-01 --date-to 2026-07-31'
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import urllib.request

ISS = "https://iss.moex.com/iss"


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def weights() -> list[tuple[str, float]]:
    """Текущий состав индекса РТС с весами, % капитализации.

    Эндпоинт страничный и по умолчанию отдаёт ~20 строк в алфавитном порядке: без
    пагинации корзина молча собиралась бы из первых по алфавиту бумаг на ~39% веса
    (и сверка с RTSI показывала бы фальшиво низкую корреляцию)."""
    rows, start = [], 0
    while True:
        a = _get(f"{ISS}/statistics/engines/stock/markets/index/analytics/RTSI.json"
                 f"?iss.meta=off&start={start}")["analytics"]
        page = [dict(zip(a["columns"], r)) for r in a["data"]]
        rows += page
        if not page:
            break
        start += len(page)
    last = max(r["tradedate"] for r in rows)
    return [(r["ticker"], float(r["weight"])) for r in rows if r["tradedate"] == last]


def closes(secid: str, engine: str, market: str, board: str, frm: str, till: str) -> dict[str, float]:
    """Дневные закрытия по торговой истории (LEGALCLOSEPRICE, иначе CLOSE)."""
    out, start = {}, 0
    while True:
        j = _get(f"{ISS}/history/engines/{engine}/markets/{market}/boards/{board}"
                 f"/securities/{secid}.json?iss.meta=off&from={frm}&till={till}&start={start}")
        h = j["history"]
        rows = [dict(zip(h["columns"], r)) for r in h["data"]]
        for r in rows:
            px = r.get("LEGALCLOSEPRICE") or r.get("CLOSE")
            if px:
                out[r["TRADEDATE"]] = float(px)
        if len(h["data"]) < 100:
            return out
        start += len(h["data"])


def rets(series: dict[str, float], days: list[str]) -> list[float | None]:
    return [None if (days[i - 1] not in series or days[i] not in series or not series[days[i - 1]])
            else series[days[i]] / series[days[i - 1]] - 1.0
            for i in range(1, len(days))]


def corr(a: list[float], b: list[float]) -> float:
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date-from", required=True)
    ap.add_argument("--date-to", required=True)
    ap.add_argument("--top", type=int, default=0, help="взять только N крупнейших бумаг (0 = все)")
    a = ap.parse_args()
    frm, till = a.date_from, a.date_to

    w = sorted(weights(), key=lambda x: -x[1])
    if a.top:
        w = w[:a.top]
    print(f"бумаг в корзине: {len(w)}, сумма весов {sum(x[1] for x in w):.2f}%")
    print("крупнейшие:", ", ".join(f"{t} {v:.2f}%" for t, v in w[:8]))

    idx = closes("RTSI", "stock", "index", "RTSI", frm, till)
    fx = closes("USD000UTSTOM", "currency", "selt", "CETS", frm, till)
    days = sorted(set(idx) & set(fx))
    print(f"торговых дней: {len(days)}")

    px = {}
    for t, _ in w:
        try:
            px[t] = closes(t, "stock", "shares", "TQBR", frm, till)
        except Exception as exc:                                   # noqa: BLE001
            print(f"  пропуск {t}: {exc}")

    ir, fr = rets(idx, days), rets(fx, days)
    br: list[float | None] = []
    for i in range(len(days) - 1):
        tot, acc = 0.0, 0.0
        for t, wt in w:
            s = px.get(t, {})
            p0, p1 = s.get(days[i]), s.get(days[i + 1])
            if p0 and p1:
                acc += wt * (p1 / p0 - 1.0)
                tot += wt
        br.append(acc / tot if tot else None)

    ok = [i for i in range(len(ir)) if ir[i] is not None and br[i] is not None and fr[i] is not None]
    IX = [ir[i] for i in ok]
    B = [br[i] for i in ok]
    F = [fr[i] for i in ok]
    BUSD = [(1 + B[i]) / (1 + F[i]) - 1 for i in range(len(ok))]     # корзина, пересчитанная в доллары

    print(f"\n--- 1. ручная корзина против индекса, дневные доходности ({len(ok)} дней) ---")
    print(f"corr(рублёвая корзина, RTSI)        = {corr(B, IX):.4f}")
    print(f"corr(корзина в долларах, RTSI)      = {corr(BUSD, IX):.4f}")
    print(f"СКО расхождения, п.п. в день        = {st.pstdev([B[i] - IX[i] for i in range(len(ok))]) * 100:.3f}"
          f" (рубли) / {st.pstdev([BUSD[i] - IX[i] for i in range(len(ok))]) * 100:.3f} (доллары)")
    print("Дневной тест шумный: закрытие TQBR, закрытие индекса и закрытие USD/RUB это")
    print("три разных момента времени, поэтому вычитание курса добавляет шума не меньше,")
    print("чем убирает. Решающий тест ниже — он не зависит от совпадения моментов.")

    # 2. Долларовость RTSI как ОТНОШЕНИЕ уровней: если RTSI долларовый, то
    #    IMOEX / (RTSI × USD/RUB) обязано быть почти константой, а IMOEX / RTSI — нет.
    im = closes("IMOEX", "stock", "index", "SNDX", frm, till)
    dd = sorted(set(im) & set(idx) & set(fx))
    if dd:
        r1 = [im[k] / idx[k] for k in dd]
        r2 = [im[k] / (idx[k] * fx[k]) for k in dd]
        cv1 = st.pstdev(r1) / st.mean(r1)
        cv2 = st.pstdev(r2) / st.mean(r2)
        print(f"\n--- 2. в какой валюте посчитан RTSI ({len(dd)} дней) ---")
        print(f"IMOEX / RTSI              разброс {min(r1):.4f}..{max(r1):.4f}  СКО/сред = {cv1:.4f}")
        print(f"IMOEX / (RTSI × USD/RUB)  разброс {min(r2):.5f}..{max(r2):.5f}  СКО/сред = {cv2:.4f}")
        print("Чем меньше СКО/сред, тем ближе к константе, то есть к верной формуле.")
        print("RTSI ДОЛЛАРОВЫЙ" if cv2 < cv1 else "RTSI РУБЛЁВЫЙ",
              f"(множитель курса снижает разброс в {cv1 / cv2:.1f} раза)" if cv2 < cv1 else "")

    print("\nЧто это значит для Shaving RI 1: RI и RTSI оба долларовые, поэтому отношение")
    print("RI/RTSI от курса НЕ зависит и валютную ногу тащить не нужно. Ручная рублёвая")
    print("сумма Σ вес×цена такой защиты не даёт: она разъезжается с фьючерсом ровно на")
    print("движение USD/RUB. Это вторая причина брать опорной серией готовый RTSI.")


if __name__ == "__main__":
    main()
