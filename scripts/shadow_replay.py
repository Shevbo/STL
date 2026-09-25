#!/usr/bin/env python3
"""Теневой прогон дня: что робот сделал БЫ, если бы сбоя не было.

    python scripts/shadow_replay.py 2026-09-25 --robot lxk22tsffsxiiotb8kmpsato

ЗАЧЕМ. 25.09.2026 торговля встала: терминал рвал связь, данных не было часами,
роботы стояли на паузе. Их фактический результат за день вышел ХОРОШИМ — шорт
досидел до падения, — но это заслуга простоя, а не алгоритма: закрыться по своим
правилам робот не мог, потому что не видел рынка. Оператор верно назвал это
лукавством в статистике.

ЧТО СЧИТАЕМ. Лента сделок за день у нас полная (восстановлена из выгрузки
терминала), а бары строятся ТЕМ ЖЕ кодом, что у живого раннера
(robot_runner.bars.BarBuilder), поэтому свеча в модели совпадает с боевой.
Прогоняем стратегию робота по этим барам и получаем сделки, которые он сделал бы
на непрерывных данных.

ЧЕГО НЕ ДЕЛАЕМ. Не переписываем журнал algo_trades: там реальные филлы, комиссия
и номера сделок QUIK, и подмена факта моделью лишает смысла весь учёт. Модель
живёт ОТДЕЛЬНО и показывается РЯДОМ с фактом; разница между ними — цена сбоя,
и она обязана быть видимой, а не растворённой в результате робота.

ГРАНИЦА ЧЕСТНОСТИ. Это ответ на узкий вопрос: «что сделал бы робот на полной
ленте по своим правилам». Исполнение моделируется по закрытию бара, как в
бэктесте, — реальные филлы, проскальзывание и очередь в стакане сюда не входят.
Поэтому модель годится для сравнения С САМОЙ СОБОЙ (день против дня, робот
против робота), а не как обещание денег.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robot_runner.bars import BarBuilder          # noqa: E402  — тот же билдер, что в бою
from robot_runner.host import resolve_on_bar      # noqa: E402  — то же разрешение стратегии
from trader.lab.backtest import run_single_backtest  # noqa: E402

MSK = datetime.timezone(datetime.timedelta(hours=3))
ARCHIVE = "/home/ubuntu/market-archive"


def load_bars(day: str, code: str, archive: str = ARCHIVE) -> list:
    """Лента дня -> закрытые минутные бары билдером раннера."""
    path = os.path.join(archive, f"trade-{day}.jsonl")
    if not os.path.exists(path):
        raise SystemExit(f"нет ленты за {day}: {path}")
    b = BarBuilder(max_bars=5000)
    seen = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("code") != code:
                continue
            b.on_trade(int(d["ts_ms"]), float(d["price"]), int(d.get("qty") or 0))
            seen += 1
    if not seen:
        raise SystemExit(f"в ленте за {day} нет сделок по {code}")
    return b.bars()


def fact_from_ledger(day: str, robot_id: str) -> dict | None:
    """Факт дня из журнала алготорговли — для строки сравнения. Журнал мог не
    добрать сделки за время обрыва: тогда и он занижен, и это видно по числу."""
    try:
        import asyncio

        import asyncpg

        from trader.config import Settings
    except ImportError:
        return None

    async def go():
        lo = int(datetime.datetime.fromisoformat(day).replace(tzinfo=MSK).timestamp() * 1000)
        hi = lo + 86_400_000
        pool = await asyncpg.create_pool(Settings().lab_db_url, min_size=1, max_size=2)
        rows = await pool.fetch(
            "SELECT count(*) AS fills, coalesce(sum(pnl_net_rub),0) AS net "
            "FROM algo_trades WHERE robot_id=$1 AND mode='real' "
            "AND ts_ms >= $2 AND ts_ms < $3", robot_id, lo, hi)
        await pool.close()
        return {"fills": int(rows[0]["fills"]), "net_rub": float(rows[0]["net"])}

    try:
        return asyncio.run(go())
    except Exception:  # noqa: BLE001 — без БД сравнение всё равно печатаем
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day", help="дата МСК, YYYY-MM-DD")
    ap.add_argument("--robot", required=True)
    ap.add_argument("--archive", default=ARCHIVE)
    ap.add_argument("--specs", default="/home/ubuntu/robots-backup-20260925.json",
                    help="выгрузка зеркала роботов (robots-mirror) со спеками")
    # ₽ за пункт: модель считает деньги, а пункт не рубль. Для RI берётся из
    # params агента; без него результат остался бы в пунктах под видом рублей.
    ap.add_argument("--point-value", default="1.6879",
                    help="₽ за пункт инструмента (RIZ6 ≈ 1.6879)")
    args = ap.parse_args()

    with open(args.specs, encoding="utf-8") as fh:
        mirror = json.load(fh)
    spec = next((r for r in mirror.get("robots", [])
                 if str(r.get("robot_id") or r.get("id")) == args.robot), None)
    if spec is None:
        raise SystemExit(f"робота {args.robot} нет в {args.specs}")

    params = json.loads(spec.get("params_json") or "{}")
    code = str(spec.get("symbol") or params.get("symbol") or "")
    strategy = str(spec.get("strategy_id") or params.get("strategy_id") or "")
    if not strategy:
        raise SystemExit("в спеке робота нет strategy_id")

    bars = load_bars(args.day, code, args.archive)
    print(f"{args.robot}: {code}, стратегия {strategy}, баров за день {len(bars)}")

    # Стратегия берётся ТЕМ ЖЕ разрешением, что у живого раннера: реестровая —
    # через make_on_bar, отдельная — модулем. Иначе сравнивали бы с чужим кодом.
    class _Mod:
        on_bar = staticmethod(resolve_on_bar(strategy))

    res = asyncio.run(run_single_backtest(_Mod, bars, code, params,
                                          point_value=float(args.point_value)))
    trades = res.get("trades") or []
    model_net = float(res.get("net_profit") or 0)
    print("\nМОДЕЛЬ (полная лента, правила робота, исполнение по закрытию бара):")
    print(f"   сделок {len(trades)}, net {model_net:,.0f} ₽, "
          f"win {res.get('win_rate')}, просадка {res.get('max_drawdown')}")
    for t in trades[:12]:
        ts = datetime.datetime.fromtimestamp(int(t["time"]), MSK).strftime("%H:%M")
        print(f"     {ts} {t.get('side', '')} {t.get('qty', '')} @ "
              f"{t.get('price', '')} pnl {t.get('pnl', 0):,.0f}")

    fact = fact_from_ledger(args.day, args.robot)
    if fact is not None:
        print("\nФАКТ (журнал алготорговли):")
        print(f"   филлов {fact['fills']}, net {fact['net_rub']:,.0f} ₽")
        print(f"\nЦЕНА СБОЯ за день: {fact['net_rub'] - model_net:+,.0f} ₽ "
              "(факт минус модель)")
        if fact["fills"] == 0:
            print("   ВНИМАНИЕ: в журнале за день НЕТ филлов этого робота — "
                  "сравнивать не с чем, журнал не добрал сделки за обрыв связи.")
    print("\nМодель — не обещание денег: исполнение по закрытию бара, без "
          "проскальзывания и очереди в стакане. Журнал не тронут.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
