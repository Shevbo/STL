"""Склейка минуток FORTS по контрактам в формате Финам/TSLab (как RI.txt), с SECNAME
в каждой строке, чтобы regime_step0.py резал по контрактам без швов (15.09.2026).

Фронт дня = контракт с наибольшим объёмом за день, и склейка вперёд не возвращается
на более ранний контракт. Даты экспирации не нужны: у истёкших контрактов ISS их не
отдаёт (см. rf_fetch_contracts.py). Качает ISS, поэтому запуск там, где ISS доступен
(hoster); это загрузка, не расчёт. Бары каждого контракта сразу пишутся в файл рядом,
в памяти только дневные объёмы: на hoster earlyoom при нехватке памяти убивает STL.

    PYTHONPATH=~/apps/shectory-trader $PY fetch_splice_txt.py GD 2022-01-03 2026-09-14 GD.txt
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from trader.lab.iss_loader import IssLoader

LETTERS = "FGHJKMNQUVXZ"
QUARTERLY = {"GD": "HMUZ", "RI": "HMUZ", "Si": "HMUZ", "MX": "HMUZ"}


def candidates(base: str, d_from: date, d_to: date):
    letters = QUARTERLY.get(base, LETTERS)
    lead = 5 if base in QUARTERLY else 3          # месяцев до поставки, с запасом
    for y in range(d_from.year, d_to.year + 2):
        for m, ch in enumerate(LETTERS, 1):
            if ch not in letters:
                continue
            end = date(y, m, 28)
            start = end - timedelta(days=31 * lead)
            if end < d_from or start > d_to + timedelta(days=62):
                continue
            yield f"{base}{ch}{y % 10}", max(start, d_from), min(end + timedelta(days=4), d_to)


async def main() -> None:
    base, d_from, d_to, out = sys.argv[1], date.fromisoformat(sys.argv[2]), date.fromisoformat(sys.argv[3]), sys.argv[4]
    tmp = out + ".parts"
    os.makedirs(tmp, exist_ok=True)
    vol, order = defaultdict(lambda: defaultdict(int)), []
    async with IssLoader() as ld:
        for secid, a, b in candidates(base, d_from, d_to):
            bs = await ld.fetch_contract_bars(secid, a, b, 1)
            print(f"{secid} {a}..{b}: {len(bs)}", flush=True)
            if not bs:
                continue
            order.append(secid)
            with open(os.path.join(tmp, secid), "w", encoding="utf-8") as f:
                for x in bs:
                    t = datetime.fromtimestamp(x.time, timezone.utc)
                    vol[t.date()][secid] += x.volume
                    f.write(f"{t:%Y%m%d},{t:%H%M%S},{x.open:g},{x.high:g},{x.low:g},{x.close:g},{x.volume}\n")
            del bs
    front, last_idx = {}, -1
    for d in sorted(vol):
        best = max(vol[d], key=vol[d].get)
        idx = max(order.index(best), last_idx)      # назад не откатываемся
        front[f"{d:%Y%m%d}"], last_idx = order[idx], idx
    n = 0
    with open(out, "w", encoding="utf-8", newline="\n") as fo:
        fo.write("<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<SECNAME>\n")
        for secid in order:                          # порядок контрактов = порядок времени
            with open(os.path.join(tmp, secid), encoding="utf-8") as f:
                for ln in f:
                    if front.get(ln[:8]) == secid:
                        fo.write(f"{base},1,{ln.rstrip()},{secid}\n")
                        n += 1
            os.remove(os.path.join(tmp, secid))
    os.rmdir(tmp)
    seg = []
    for d in sorted(front):
        if not seg or seg[-1][0] != front[d]:
            seg.append([front[d], d, d])
        seg[-1][2] = d
    print(f"строк {n}; сегменты: " + ", ".join(f"{s}:{a[2:]}-{b[2:]}" for s, a, b in seg))


if __name__ == "__main__":
    asyncio.run(main())
