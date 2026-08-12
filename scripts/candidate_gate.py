#!/usr/bin/env python3
"""Гейт кандидата: отсеять всё, что уже трижды заворачивало окно real-trade.

Зачем. Отбор и проверка были разнесены: backtests ранжировал по net, real-trade
проверял постфактум и находил вырожденность, краевой оптимум или 44 сделки. Три
кандидата подряд получили «нет». Разногласие возникало не от разных взглядов, а
от того, что проверки не было ВНУТРИ отбора. Здесь она внутри.

Каждый пункт рождён конкретным инцидентом, а не общими соображениями — см.
комментарии у проверок. Уровень 1 дешёвый (запрос + REGISTRY), уровень 2 требует
прогонов и живёт отдельно: сюда попадает только то, что прошло первый.

Запуск НА ХОСТЕРЕ (ISS с dev-бокса недоступна):
  cd ~/apps/shectory-trader
  PYTHONPATH=. python scripts/candidate_gate.py --campaign camp-20260810%honest%
  # опции: --min-trades 150 --min-nm 3 --min-windows 3 --limit 40
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402

from trader.config import Settings  # noqa: E402
from trader.lab.strategies.library import REGISTRY  # noqa: E402

# Раннер персистит 600 закрытых баров (robot_runner/bars.py, to_rows). Строка,
# которой нужно больше, после каждого рестарта агента слепа часами: в бэктесте
# число честное, в бою недостижимое.
RUNNER_BAR_TAIL = 600

# Инструменты, которые агент реально может торговать. GDU6/MXU6 не в фиде и не в
# белом списке лимитов — их незачем и отбирать (замечание real-trade 11.08).
TRADABLE = {"RIU6", "RIM6", "BRU6", "BRQ6", "SiU6", "GZU6", "SVU6", "NGQ6", "MMU6"}


def max_position(params: dict) -> int:
    """Абсолютный потолок позиции, а не только лестницы усреднения.

    avg_max — потолок ДОБОРОВ; абсолютный максимум задаёт ещё и ставочная
    система (qty + bet_max). Берём худшее из двух, потому что закрываться робот
    будет из фактической позиции, а не из той, что мы имели в виду.
    """
    def num(k, d=0):
        try:
            return int(float(params.get(k, d) or d))
        except (TypeError, ValueError):
            return d
    qty = max(1, num("qty", 1))
    return max(num("avg_max", 1), qty + num("bet_max", 0))


def caps_ok(params: dict, per_order: int, working: int) -> str | None:
    """Сможет ли робот ЗАКРЫТЬСЯ. Проверка фида и белого списка этого не ловит.

    Выход всей книгой — ОДНА заявка, а разворот держит в воздухе выход и новый
    вход. Поэтому max_position <= кап на заявку И max_position + qty <= кап
    рабочих контрактов. Нарушение означает робота, который открывается
    усреднением и не закрывается никогда: било вживую 21.07.2026.
    """
    pos = max_position(params)
    try:
        qty = max(1, int(float(params.get("qty", 1) or 1)))
    except (TypeError, ValueError):
        qty = 1
    if pos > per_order:
        return f"позиция {pos} > капа на заявку {per_order}: не закроется одной заявкой"
    if pos + qty > working:
        return f"позиция {pos} + вход {qty} > капа рабочих {working}: разворот не поместится"
    return None


def margin_ok(params: dict, margin_per_contract: float, free_margin: float,
              multiplier: float, share: float) -> str | None:
    """Влезает ли ПОЛНЫЙ набор в деньги счёта.

    Половина свободного ГО, а не всё: остаток обязан покрыть просадку открытой
    позиции, иначе принудительное закрытие в худшей точке. Считать по ГО СЧЁТА,
    а не биржевому — брокер берёт кратно (множитель 2.4), и на биржевом значении
    ёмкость счёта завышена ровно во столько же.
    """
    if not margin_per_contract or not free_margin:
        return None                      # экономика инструмента неизвестна — не судим
    need = max_position(params) * margin_per_contract * multiplier
    if need > free_margin * share:
        return (f"лестница требует {need:,.0f} ГО счёта > {share:.0%} свободного "
                f"({free_margin * share:,.0f})")
    return None


def spread_cost(params: dict, trades: int, step_value: float) -> float:
    """Во сколько обойдётся ПЕРЕХОД СПРЕДА, которого в бэктесте нет вовсе.

    Комиссия в бэктесте есть (тейкерская), а спред — нет: заявки робота
    маркетабельны по построению, значит на каждой стороне теряется минимум шаг
    цены. Чем чаще конфиг торгует, тем сильнее это его режет, и это правильно.
    """
    if not step_value or not trades:
        return 0.0
    try:
        qty = max(1, int(float(params.get("qty", 1) or 1)))
    except (TypeError, ValueError):
        qty = 1
    return trades * 2 * qty * step_value


def warmup_bars(strategy: str, params: dict) -> int | None:
    """Прогрев ПО САМОЙ СТРАТЕГИИ, а не по формуле над ключами params.

    Бланкетное 4*max(ema1,ema2,fast,slow) неверно дважды: SMA-семейству
    (triple_sma, ema_atr) хватает max+2, потому что SMA полностью определена на
    своём окне, а pivot_reversal вовсе не имеет ключей периодов — GREATEST от
    несуществующих дал бы ноль, и строка прошла бы насквозь при реальных 2200
    барах. Решение подсказал ui-ux, реализуя ту же проверку в лампе компаньона.
    """
    base = strategy[:-len("__inv")] if strategy.endswith("__inv") else strategy
    spec = REGISTRY.get(base)
    if spec is None:
        return None                      # стратегия-модуль вне реестра — не судим
    try:
        return int(spec["warmup"](params))
    except Exception:                    # noqa: BLE001 — битые params не режем
        return None


def degenerate(params: dict) -> str | None:
    """Пары периодов, при которых осциллятора нет, а знак есть.

    EMA(n) против EMA(n) не пересекается никогда: сравнение всегда ложно, знак
    залипает навсегда. Пять бумажных роботов стояли в вечном шорте, а лидеры
    MXU6 показывали max_mae РОВНО 0 при RF 50 262. Для ema режем только
    РАВЕНСТВО: ema1 > ema2 — законная инверсия, фейд бывает единственным
    прибыльным режимом.
    """
    def num(k):
        v = params.get(k)
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    e1, e2 = num("ema1"), num("ema2")
    if e1 is not None and e2 is not None and e1 == e2:
        return f"ema1==ema2=={e1:g}"
    f, s = num("fast"), num("slow")
    if f is not None and s is not None and f >= s:
        return f"fast>=slow ({f:g}>={s:g})"
    return None


def on_grid_edge(params: dict, axes: dict[str, list[float]]) -> list[str]:
    """Оси, по которым строка стоит на КРАЮ перебранного диапазона.

    Оптимум на границе — не оптимум, а обрыв: настоящий может лежать за ней, а
    может не существовать вовсе. У лидера GDU6 на максимуме стояли ПЯТЬ осей
    сразу (mult, period, avg_max, avg_step_atr, avg_atr_n), и разбор показал
    монотонный склон «чем реже торгуем, тем больше net» — то есть стену
    комиссии, а не настройку. Ось из одного значения (пин) краем не считается.
    """
    edges = []
    for key, vals in axes.items():
        if len(vals) < 2:
            continue
        try:
            v = float(params.get(key))
        except (TypeError, ValueError):
            continue
        if v == min(vals) or v == max(vals):
            edges.append(f"{key}={v:g}")
    return edges


async def main() -> int:
    ap = argparse.ArgumentParser(description="Гейт кандидата уровня 1")
    ap.add_argument("--campaign", required=True, help="LIKE-шаблон campaign_run")
    ap.add_argument("--min-trades", type=int, default=150, help="на ВЕСЬ прогон")
    # Порог на окно, а не только на прогон: 150 суммарно может быть одним взрывным
    # окном и тремя пустыми. Но per-window порог НЕЛЬЗЯ ставить высоким — 150 на
    # каждом окне отклоняет нашего единственного кандидата, прошедшего и второй
    # контракт, и зеркальный тест (291 сделка = 73 на окно). Ловим 44-сделочные
    # миражи (11 на окно), а не нормальную частоту. Считается приближённо:
    # в лидерборде нет сделок по окнам, есть только total_trades и windows_total.
    ap.add_argument("--min-trades-window", type=int, default=20)
    ap.add_argument("--min-nm", type=float, default=3.0, help="порог net/max_mae")
    ap.add_argument("--min-windows", type=int, default=3)
    ap.add_argument("--max-edges", type=int, default=1, help="сколько осей на краю терпим")
    # Капы агента и деньги счёта: строка, которую нельзя ни закрыть, ни оплатить,
    # не кандидат, сколько бы она ни показывала.
    ap.add_argument("--cap-per-order", type=int,
                    default=int(os.environ.get("QUIK_MAX_CONTRACTS_PER_ORDER", 34)))
    ap.add_argument("--cap-working", type=int,
                    default=int(os.environ.get("QUIK_MAX_WORKING_CONTRACTS", 70)))
    ap.add_argument("--free-margin", type=float, default=1_529_640.0,
                    help="ХУДШЕЕ наблюдавшееся свободное ГО: оператор торгует руками "
                         "на том же счёте, и оно плавает в разы за вечер")
    ap.add_argument("--margin-share", type=float, default=0.5,
                    help="какую долю свободного ГО разрешено занять полным набором")
    ap.add_argument("--margin-multiplier", type=float,
                    default=float(os.environ.get("QUIK_MARGIN_MULTIPLIER", 2.4)))
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--json", action="store_true", help="выдать прошедших в JSON для уровня 2")
    args = ap.parse_args()

    pool = await asyncpg.create_pool(Settings().lab_db_url)
    async with pool.acquire() as c:
        rows = await c.fetch(
            """SELECT strategy, symbol, params, net_profit, total_trades, max_mae,
                      windows_profitable, windows_total, degrade, recovery_factor
                 FROM optimization_leaderboard
                WHERE campaign_run LIKE $1""",
            args.campaign,
        )
        # Экономика инструмента: ГО контракта для проверки ёмкости счёта и
        # РУБЛЁВАЯ стоимость шага цены для перехода спреда.
        meta = {r["symbol"]: dict(r) for r in await c.fetch(
            "SELECT symbol, initial_margin, price_step_value FROM instrument_meta")}
    await pool.close()
    if not rows:
        print(f"нет строк по шаблону {args.campaign!r}")
        return 1

    recs = []
    for r in rows:
        p = r["params"]
        recs.append({**dict(r), "params": json.loads(p) if isinstance(p, str) else dict(p)})

    # Оси перебора берём из САМИХ строк: сетка нигде не хранится, а какие значения
    # реально считались — видно только по результатам.
    axes: dict[tuple[str, str], dict[str, list[float]]] = {}
    for rec in recs:
        key = (rec["strategy"], rec["symbol"])
        a = axes.setdefault(key, {})
        for k, v in rec["params"].items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            a.setdefault(k, set()).add(fv)  # type: ignore[union-attr]
    for a in axes.values():
        for k in a:
            a[k] = sorted(a[k])  # type: ignore[arg-type]

    # Центр группы. Верхняя строка при глубоко отрицательной медиане соседей —
    # выброс, а не находка: у GDU6 bollinger_bo медиана 0 и среднее −117 512 при
    # лидере +358 174.
    centre = {
        key: statistics.median([x["net_profit"] or 0 for x in recs
                                if (x["strategy"], x["symbol"]) == key])
        for key in axes
    }

    passed, rejected = [], {}
    for rec in recs:
        p, why = rec["params"], []          # why: список (ключ причины, подробность)
        key = (rec["strategy"], rec["symbol"])

        if rec["symbol"] not in TRADABLE:
            why.append(("инструмент не торгуется агентом", rec["symbol"]))
        d = degenerate(p)
        if d:
            why.append(("вырожденная пара периодов", d))
        w = warmup_bars(rec["strategy"], p)
        if w is not None and w > RUNNER_BAR_TAIL:
            why.append(("прогрев не влезает в персист раннера", f"{w} > {RUNNER_BAR_TAIL}"))
        trades = rec["total_trades"] or 0
        if trades < args.min_trades:
            why.append((f"сделок меньше {args.min_trades}", str(trades)))
        wt = rec["windows_total"] or 4
        if wt and trades / wt < args.min_trades_window:
            why.append((f"сделок на окно меньше {args.min_trades_window}",
                        f"{trades / wt:.0f}"))
        cap = caps_ok(p, args.cap_per_order, args.cap_working)
        if cap:
            why.append(("не закроется: капы агента", cap))
        m = meta.get(rec["symbol"]) or {}
        mg = margin_ok(p, float(m.get("initial_margin") or 0), args.free_margin,
                       args.margin_multiplier, args.margin_share)
        if mg:
            why.append(("лестница не влезает в свободное ГО", mg))
        # Спред: в бэктесте его нет вовсе, а робот кроссит его на каждой стороне.
        sp = spread_cost(p, trades, float(m.get("price_step_value") or 0))
        if sp and (rec["net_profit"] or 0) - sp <= 0:
            why.append(("net не выживает после перехода спреда",
                        f"{rec['net_profit']:,.0f} − {sp:,.0f} <= 0"))
        if (rec["net_profit"] or 0) <= 0:
            why.append(("net <= 0", ""))
        mae = rec["max_mae"] or 0
        if mae <= 0:
            why.append(("max_mae не измерен (нулевая просадка = мираж)", ""))
        elif (rec["net_profit"] or 0) / mae < args.min_nm:
            why.append((f"net/MAE меньше {args.min_nm}", f"{(rec['net_profit'] or 0)/mae:.2f}"))
        if (rec["windows_profitable"] or 0) < args.min_windows:
            why.append((f"прибыльных окон меньше {args.min_windows}",
                        f"{rec['windows_profitable']}/{rec['windows_total']}"))
        edges = on_grid_edge(p, axes[key])
        if len(edges) > args.max_edges:
            why.append(("оптимум на краю сетки", f"{len(edges)} осей: {', '.join(edges)}"))
        if centre[key] < 0 and (rec["net_profit"] or 0) > 0 and abs(centre[key]) > (rec["net_profit"] or 0):
            why.append(("выброс: медиана группы глубже собственного net",
                        f"{centre[key]:,.0f}"))

        if why:
            for reason, _ in why:
                rejected[reason] = rejected.get(reason, 0) + 1
        else:
            passed.append(rec)

    passed.sort(key=lambda r: (r["net_profit"] or 0) / (r["max_mae"] or 1), reverse=True)

    if args.json:
        print(json.dumps([{ "strategy": r["strategy"], "symbol": r["symbol"],
                            "params": r["params"], "net": r["net_profit"],
                            "trades": r["total_trades"], "mae": r["max_mae"]}
                          for r in passed[:args.limit]], ensure_ascii=False, indent=1))
        return 0

    print(f"строк в кампании: {len(recs)}   ПРОШЛИ УРОВЕНЬ 1: {len(passed)}\n")
    print("отсев по причинам (строка могла попасть в несколько):")
    for reason, n in sorted(rejected.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>6}  {reason}")
    if not passed:
        print("\nНИ ОДНОГО КАНДИДАТА. Это тоже результат — на этих данных честного нет.")
        return 0
    print(f"\n{'стратегия':18} {'симв':6} {'сделок':>7} {'net':>12} {'MAE':>10} "
          f"{'net/MAE':>8} {'окна':>6}")
    for r in passed[:args.limit]:
        print(f"{r['strategy']:18} {r['symbol']:6} {r['total_trades']:>7} "
              f"{r['net_profit']:>12,.0f} {r['max_mae']:>10,.0f} "
              f"{(r['net_profit'] / r['max_mae']):>8.2f} "
              f"{r['windows_profitable']}/{r['windows_total']:>4}")
    print("\nДальше — уровень 2 (прогоны): пересчёт текущим кодом, зеркальная сторона,")
    print("второй контракт, концентрация по дням и сделкам. Без него кандидат НЕ готов.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
