"""Добор РЕАЛЬНЫХ сделок роботов в algo_trades за период обрыва связи STL-агент.

ЗАЧЕМ. Журнал алготорговли пополняется только из ЖИВОГО зеркала агента
(trader/api/app.py::_algo_ledger_ingest -> algo_ledger.ingest_once, раз в 30 с).
Пока связи нет, роботы внутри агента торгуют, а журнал стоит. Догнать пропущенное
из ринга агента можно лишь пока сделка в нём лежит: наружу, в status_json, агент
отдаёт ХВОСТ В 100 СДЕЛОК СЧЁТА (quikTradesCap, internal/status/status.go), и это
хвост ВСЕГО счёта, вместе с ручными сделками оператора. Спокойный день (25.09.2026
— 85 сделок за сутки) переживается без потерь, оборотистый — нет: к моменту
восстановления связи начало дыры уже вытеснено, и в ринге его больше никогда не
будет. Заживление филлов на агенте (Journal Auto-Heal) тут не помогает: оно
лечит КНИГУ РОБОТА на VDS, а не журнал в БД, и про algo_trades не знает.

ИСТОЧНИК ДОБОРА — суточный журнал сделок data/trades/YYYY-MM-DD.jsonl
(trader/quik/truth.py): те же сделки QUIK с тегом (brokerref), но записанные на
диск навсегда, а не в ринг. Полная лента дня из выгрузки терминала
(market-archive/trade-*.jsonl) для добора НЕ ГОДИТСЯ: в ней нет тегов, принадлежность
роботу по ней не определяется.

ДЕДУП ОБЯЗАТЕЛЕН. 24.07.2026 два бэкфилла записали одну сделку дважды (ключи
`lg:` и `bf:` описывали один филл, UNIQUE этого не видит): реплей прочёл копию
как долив, роли поехали OPEN->AVR, позиция разошлась на её объём. Поэтому здесь
(1) ключ тот же, что у живого ингеста — `q:<номер сделки>`, значит повторный
запуск и последующий живой проход физически не могут вставить второй раз, и
(2) сверх того прогоняется drop_already_ledgered — на случай, если тот же филл уже
лежит под другим ключом.

ПЕРЕСЧЁТ ХВОСТА. Дыра вставляется в СЕРЕДИНУ истории: у строк, лежащих после неё,
pos_after/avg_after и вся арифметика посчитаны от позиции без добираемых сделок.
Поэтому после вставки хвост робота переигрывается той же price_row и расходящиеся
строки обновляются. Опора реплея — последняя строка ПЕРЕД дырой; если её нет
(дыра старше всей истории робота), робот пропускается: позиция на тот момент
неизвестна, а угадывать её в денежном журнале нельзя.

Запуск на хостере (по умолчанию НИЧЕГО НЕ ПИШЕТ, только отчёт):
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $(~/.local/bin/poetry env info --path)/bin/python \
        scripts/backfill_ledger_gap.py --from "2026-09-25 13:00" --to "2026-09-25 16:10"
Запись — тем же вызовом с --apply.
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.quik.algo_ledger import (  # noqa: E402
    RawFill,
    drop_already_ledgered,
    price_row,
)

MSK_OFFSET_MS = 3 * 3600 * 1000
TRADES_DIR = "data/trades"
_COLS = ("robot_id", "mode", "ts_ms", "trade_num", "order_num", "symbol", "side",
         "qty", "price", "order_kind", "point_value", "pnl_gross_rub",
         "commission_rub", "pnl_net_rub", "pos_after", "avg_after", "dedup_key")


def msk_ms(text: str) -> int:
    """'2026-09-25 13:00' (МСК) -> epoch ms. Оператор думает в МСК, БД — в UTC."""
    fmt = "%Y-%m-%d %H:%M" if " " in text else "%Y-%m-%d"
    return calendar.timegm(time.strptime(text, fmt)) * 1000 - MSK_OFFSET_MS


def msk_str(ts_ms: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.gmtime((ts_ms + MSK_OFFSET_MS) / 1000))


def read_journal(t0_ms: int, t1_ms: int, directory: str = TRADES_DIR) -> list[dict]:
    """Сделки из суточных журналов, попавшие в окно, дедуп по номеру QUIK.

    Читаются ВСЕ файлы каталога, а не только файл нужной даты: журнал пишется по
    дате ЗАПИСИ, и при смене суток в новый файл перезаписывается хвост ринга —
    вечерняя сделка живёт в двух файлах, а иногда только в следующем."""
    uniq: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.jsonl"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:  # noqa: BLE001 — битая строка не повод терять журнал
                    continue
                ts = int(row.get("ts_ms") or 0)
                num = str(row.get("num") or "")
                if num and t0_ms <= ts <= t1_ms:
                    uniq[num] = row
    return sorted(uniq.values(), key=lambda r: (int(r["ts_ms"]), str(r["num"])))


def _fill_of_ledger_row(row: dict) -> RawFill:
    return RawFill(
        robot_id=row["robot_id"], mode=row["mode"], ts_ms=int(row["ts_ms"]),
        trade_num=row.get("trade_num"), order_num=row.get("order_num"),
        symbol=row["symbol"], side=row["side"], qty=int(row["qty"]),
        price=float(row["price"]), dedup_key=row["dedup_key"])


def plan_robot(rid: str, journal: list[dict], ledger: list[dict], pv: float
               ) -> tuple[list[dict], list[dict], list[str], tuple[int, float, int]]:
    """Что добрать по ОДНОМУ роботу: (вставить, обновить, заметки, состояние_после).

    journal — сделки окна с тегом этого робота; ledger — ВСЯ его история в
    algo_trades, по возрастанию (ts_ms, seq). Обновляемые строки несут 'seq'.
    состояние_после — (позиция, средняя, время входа) на конец переигранного
    хвоста: им выравнивается algo_ledger_state, иначе следующий живой филл
    посчитается от позиции без добранного."""
    have_keys = {r["dedup_key"] for r in ledger}
    cand = [RawFill(robot_id=rid, mode="real", ts_ms=int(t["ts_ms"]),
                    trade_num=str(t["num"]), order_num=str(t.get("order_num") or "") or None,
                    symbol=t.get("sec") or "", side=t.get("side") or "",
                    qty=int(t.get("qty") or 0), price=float(t.get("price") or 0),
                    dedup_key=f"q:{t['num']}")
            for t in journal]
    cand = [f for f in cand
            if f.dedup_key not in have_keys and f.side in ("buy", "sell")
            and f.qty > 0 and f.price > 0 and f.symbol]
    tail = _state_of(ledger)
    if not cand:
        return [], [], [], tail
    # Тот же филл может уже лежать под ДРУГИМ ключом (`lg:`/`bf:` от прошлых
    # бэкфиллов) — UNIQUE этого не видит, а двойная запись ломает реплей.
    as_rows = [{"ts_ms": f.ts_ms, "side": f.side, "qty": f.qty, "price": f.price,
                "key": f.dedup_key} for f in cand]
    keep = {r["key"] for r in drop_already_ledgered(ledger, as_rows)}
    notes = []
    if len(keep) != len(cand):
        notes.append(f"{len(cand) - len(keep)} сделок уже в журнале под другим ключом")
    cand = [f for f in cand if f.dedup_key in keep]
    if not cand:
        return [], [], notes, tail

    first_ms = min(f.ts_ms for f in cand)
    before = [r for r in ledger if int(r["ts_ms"]) < first_ms]
    after = [r for r in ledger if int(r["ts_ms"]) >= first_ms]
    if not before:
        if ledger:
            return [], [], notes + [
                "дыра старше всей истории робота в журнале — позиция на её начало "
                "неизвестна, пропуск"], tail
        pos, avg, entry_ts = 0, 0.0, 0
    else:
        anchor = before[-1]
        pos, avg = int(anchor["pos_after"]), float(anchor["avg_after"])
        # Взвешенное время входа по строке не восстановить (в algo_trades его нет).
        # Берём время опорной сделки: ошибка ограничена скальперской скидкой
        # закрывающего филла, позицию и gross она не двигает.
        entry_ts = int(anchor["ts_ms"])

    # Порядок внутри одной секунды — по ключу: в сделках QUIK номер растёт по
    # времени, так что это тот же порядок, что у живого ингеста (collect_fills).
    merged = sorted(cand + [_fill_of_ledger_row(r) for r in after],
                    key=lambda f: (f.ts_ms, f.dedup_key))
    seq_by_key = {r["dedup_key"]: int(r["seq"]) for r in after}
    inserts, updates = [], []
    for f in merged:
        row = price_row(f, pos, avg, entry_ts, pv)
        pos, avg, entry_ts = row["pos_after"], row["avg_after"], row["entry_ts_after"]
        if f.dedup_key in seq_by_key:
            row["seq"] = seq_by_key[f.dedup_key]
            updates.append(row)
        else:
            inserts.append(row)
    old_by_seq = {int(r["seq"]): r for r in after}
    updates = [u for u in updates if _changed(old_by_seq[u["seq"]], u)]
    return inserts, updates, notes, (pos, avg, entry_ts)


def _state_of(ledger: list[dict]) -> tuple[int, float, int]:
    """Состояние по последней строке журнала — им отвечаем, когда добирать нечего."""
    if not ledger:
        return 0, 0.0, 0
    last = ledger[-1]
    return int(last["pos_after"]), float(last["avg_after"]), int(last["ts_ms"])


def _changed(old: dict, new: dict) -> bool:
    return (int(old["pos_after"]) != int(new["pos_after"])
            or abs(float(old["avg_after"]) - new["avg_after"]) > 1e-6
            or abs(float(old["pnl_net_rub"]) - new["pnl_net_rub"]) > 0.005
            or abs(float(old["pnl_gross_rub"]) - new["pnl_gross_rub"]) > 0.005)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="t0", required=True, help="начало окна, МСК")
    ap.add_argument("--to", dest="t1", required=True, help="конец окна, МСК")
    ap.add_argument("--apply", action="store_true",
                    help="писать в БД (по умолчанию только отчёт)")
    ap.add_argument("--dry-run", action="store_true", help="режим по умолчанию")
    args = ap.parse_args()
    t0, t1 = msk_ms(args.t0), msk_ms(args.t1)
    dry = not args.apply

    import asyncpg
    dsn = (os.environ.get("LAB_DB_URL") or "").replace("postgresql+asyncpg", "postgresql")
    if not dsn:
        sys.exit("LAB_DB_URL not set (source ~/.shectory_trade.env)")
    journal = read_journal(t0, t1)
    print(f"окно {msk_str(t0)} .. {msk_str(t1)} МСК, сделок в журналах: {len(journal)}")

    conn = await asyncpg.connect(dsn)
    try:
        # Тег QUIK = id робота, обрезанный до 20 знаков brokerref (agent recon.quikTag).
        # Бумажные роботы заявок в QUIK не ставят, поэтому совпадение по тегу может
        # быть только реальным роботом.
        ids = [r["robot_id"] for r in await conn.fetch(
            "SELECT robot_id FROM algo_ledger_state")]
        by_tag = {rid[:20]: rid for rid in ids}
        pv_map = {r["symbol"]: float(r["point_value"]) for r in await conn.fetch(
            "SELECT DISTINCT ON (symbol) symbol, point_value FROM algo_trades "
            "ORDER BY symbol, seq DESC")}
        mine: dict[str, list[dict]] = {}
        for t in journal:
            rid = by_tag.get(str(t.get("tag") or ""))
            if rid:  # остальное — ручные сделки оператора и умные заявки
                mine.setdefault(rid, []).append(t)
        skipped = len(journal) - sum(len(v) for v in mine.values())
        print(f"из них с тегом робота: {len(journal) - skipped}, чужих: {skipped}")

        total_ins = total_net = 0
        for rid, rows in sorted(mine.items()):
            ledger = [dict(r) for r in await conn.fetch(
                "SELECT seq, robot_id, mode, ts_ms, trade_num, order_num, symbol, side,"
                " qty, price, order_kind, point_value, pnl_gross_rub, commission_rub,"
                " pnl_net_rub, pos_after, avg_after, dedup_key FROM algo_trades"
                " WHERE robot_id=$1 ORDER BY ts_ms, seq", rid)]
            secs = {r.get("sec") for r in rows}
            if len(secs) > 1:  # день переката: ₽/пункт у контрактов разный
                print(f"{rid}: в окне два инструмента {secs} — пропуск, сузьте окно")
                continue
            pv = pv_map.get(rows[0].get("sec") or "")
            if not pv:
                print(f"{rid}: нет ₽/пункт для {rows[0].get('sec')!r} — пропуск")
                continue
            inserts, updates, notes, final = plan_robot(rid, rows, ledger, pv)
            for n in notes:
                print(f"{rid}: {n}")
            if not inserts:
                print(f"{rid}: добирать нечего ({len(rows)} сделок окна уже в журнале)")
                continue
            net = sum(r["pnl_net_rub"] for r in inserts)
            drift = sum(r["pnl_net_rub"] for r in updates) - sum(
                float(o["pnl_net_rub"]) for o in ledger
                if int(o["seq"]) in {u["seq"] for u in updates})
            print(f"{rid}: +{len(inserts)} сделок, net {net:+.2f} ₽; пересчёт хвоста: "
                  f"{len(updates)} строк, {drift:+.2f} ₽; позиция "
                  f"{ledger[-1]['pos_after'] if ledger else 0} -> {final[0]}")
            for r in inserts:
                print(f"    {msk_str(r['ts_ms'])} {r['side']} {r['qty']} @ {r['price']}"
                      f" -> поз {r['pos_after']}, net {r['pnl_net_rub']:+.2f} ₽"
                      f" [{r['dedup_key']}]")
            total_ins += len(inserts)
            total_net += net + drift
            if dry:
                continue
            async with conn.transaction():
                for r in inserts:
                    await conn.execute(
                        f"INSERT INTO algo_trades ({','.join(_COLS)}) VALUES ("
                        f"{','.join(f'${i + 1}' for i in range(len(_COLS)))}) "
                        "ON CONFLICT (dedup_key) DO NOTHING",
                        *[r[c] for c in _COLS])
                for r in updates:
                    await conn.execute(
                        "UPDATE algo_trades SET pnl_gross_rub=$2, commission_rub=$3,"
                        " pnl_net_rub=$4, pos_after=$5, avg_after=$6 WHERE seq=$1",
                        r["seq"], r["pnl_gross_rub"], r["commission_rub"],
                        r["pnl_net_rub"], r["pos_after"], r["avg_after"])
                await conn.execute(
                    "UPDATE algo_ledger_state SET position=$2, avg_price=$3,"
                    " entry_ts_ms=$4 WHERE robot_id=$1", rid, *final)
        print(f"ИТОГО: {total_ins} сделок, {total_net:+.2f} ₽ "
              f"({'DRY-RUN, в БД не записано' if dry else 'записано'})")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
