"""Истина P&L реальных роботов из журнала algo_trades — цифры для панели
«Ручная коррекция позиции» (поля «реализовано gross, п.» и «комиссия, п.»).

Запуск на хостере (робот должен быть НА ПАУЗЕ в момент записи — иначе цифры
устареют между снятием и вводом):

    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a; \
      poetry run python scripts/pnl_truth.py [robot_id_prefix]

Реплей ведётся той же signed-space лестницей, что раннер и бэктест (частичное
закрытие сохраняет среднюю, разворот переоткрывает), комиссия — из рублей
журнала через point_value каждого филла. Дедуп журнала не пропускает
фантомные филлы (проверено 06.08.2026), поэтому его реплей — эталон.
"""

import asyncio
import os
import sys

import asyncpg

_FALLBACK_PV = 1.618586  # RIU6 на 06.08.2026; используется только при NULL


async def main() -> None:
    prefix = (sys.argv[1] + "%") if len(sys.argv) > 1 else "%"
    conn = await asyncpg.connect(os.environ["LAB_DB_URL"])
    ids = [r["robot_id"] for r in await conn.fetch(
        "SELECT DISTINCT robot_id FROM algo_trades WHERE mode='real' AND robot_id LIKE $1",
        prefix)]
    for rid in sorted(ids):
        rows = await conn.fetch(
            """SELECT side, qty, price, commission_rub, point_value FROM algo_trades
               WHERE robot_id=$1 AND mode='real' ORDER BY ts_ms, seq""", rid)
        signed, avg, gross, comm = 0, 0.0, 0.0, 0.0
        for f in rows:
            qty, px = int(f["qty"]), float(f["price"])
            comm += float(f["commission_rub"] or 0) / float(f["point_value"] or _FALLBACK_PV)
            delta = qty if f["side"] == "buy" else -qty
            if signed != 0 and (signed > 0) != (delta > 0):
                gross += ((px - avg) if signed > 0 else (avg - px)) * min(qty, abs(signed))
            ns = signed + delta
            if ns == 0:
                signed, avg = 0, 0.0
            elif signed != 0 and (signed > 0) == (delta > 0):
                avg = (avg * abs(signed) + px * qty) / (abs(signed) + qty)
                signed = ns
            elif signed != 0 and (ns > 0) == (signed > 0):
                signed = ns          # частичное закрытие: средняя не меняется
            else:
                signed, avg = ns, px
        print(f"{rid}")
        print(f"  позиция {signed} @ {avg:.1f}  <- поля позиции/средней")
        print(f"  реализовано gross {gross:.1f} п.  <- поле «реализовано gross, п.»")
        print(f"  комиссия {comm:.1f} п.           <- поле «комиссия, п.»")
        print(f"  (чистыми {gross - comm:.1f} п.)")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
