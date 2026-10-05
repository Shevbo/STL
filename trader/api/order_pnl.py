"""P&L КАЖДОЙ заявки по отдельности: закрытое и живая переоценка.

Заказ оператора 01.10.2026: у каждой умной заявки — своей и терминальной — надо
видеть её собственный результат, и фикс, и ВМ, в том числе у снятой.

Почему это вообще считается. Филлы умной заявки помечены её тегом
(`stl-so-<so_id>`, у детей — с суффиксом), а филлы терминальной заявки несут её
номер. То есть у каждой заявки есть СВОЙ набор сделок, и его можно проиграть
отдельно: многоразовая заявка (коридор, треугольник, сетка) за свою жизнь и
покупает, и продаёт — её фикс определён полностью. У одноразовой (стоп, тейк)
закрывать нечего, и фикс честно равен нулю, пока её ногу не закроют другой
заявкой: приписывать ей чужое закрытие было бы выдумкой.

ЧТО СЧИТАЕТСЯ ЧЕМ:
  фикс  — реализация по кругам ВНУТРИ самой заявки (algo_ledger.apply_fill,
          та же средняя, что у раннера и у журнала);
  ВМ    — переоценка её ОСТАВШЕЙСЯ позиции по текущей цене, от средней входа.
          Это «ВМ от входа», как у робота, а НЕ ВМ QUIK за сессию: у заявки нет
          клиринга, она не живёт сессиями (см. ВМ в карточке робота).

КОМИССИЯ И ЧИСТЫЙ РЕЗУЛЬТАТ (заказ оператора 04.10.2026: «показывай кол-во сделок,
размер удержанной комиссии и считай P&L за вычетом комиссии»):
  commission_rub — комиссия всех сделок заявки по МОДЕЛИ (manual_pnl.fill_commission,
          та же функция, что считает блок ручной торговли, поэтому сумма по заявкам
          сходится с блоком). Факт комиссии из QUIK агент до STL пока не доводит, так что это
          ОЦЕНКА СВЕРХУ: тейкерская ставка для каждого филла;
  net_rub — фикс минус комиссия: что заявка заработала в деньгах;
  total_rub — чистый результат с живой переоценкой: фикс + ВМ − комиссия.

Рубли считаем через ₽/пункт инструмента. Его нет — отдаём пункты и говорим об
этом полем `priced: false`: пункт не рубль, и молча выдать одно за другое нельзя.
Комиссия без ₽/пункт — `None`, а не ноль: ноль читался бы как «комиссии не было».
"""

from __future__ import annotations

from typing import Any

from trader.quik.algo_ledger import apply_fill
from trader.quik.manual_pnl import fill_commission

# Тег ребёнка умной заявки: `stl-so-<so_id>` и, у некоторых, суффикс `:gm`, `:lo`.
SMART_TAG = "stl-so-"


def order_key(fill: dict[str, Any]) -> str:
    """Чьей заявке принадлежит филл: so_id умной или номер терминальной.

    Суффикс после двоеточия отрезаем: `stl-so-4e99b8c5ef:lo` это та же заявка,
    что и `stl-so-4e99b8c5ef`, просто другая её нога. Не отрезав, одна заявка
    распалась бы на несколько строк с кусками своего же результата.
    """
    tag = str(fill.get("tag") or "")
    if tag.startswith(SMART_TAG):
        return tag[len(SMART_TAG):].split(":", 1)[0]
    num = str(fill.get("order_num") or "")
    return num or ""


def pnl_by_order(fills: list[dict[str, Any]], last_prices: dict[str, float],
                 point_values: dict[str, float]) -> dict[str, dict[str, Any]]:
    """Прогон филлов по заявкам: {ключ: {fix_rub, vm_rub, pos, avg, ...}}.

    Филлы должны идти ПО ВРЕМЕНИ: средняя и реализация зависят от порядка, и
    перемешанный вход даст другое число — правдоподобное и неверное.
    """
    state: dict[str, dict[str, Any]] = {}
    for f in sorted(fills, key=lambda x: int(x.get("ts_ms") or 0)):
        key = order_key(f)
        if not key:
            continue
        try:
            qty = int(f.get("qty") or 0)
            price = float(f.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price <= 0:
            continue
        sym = str(f.get("sec") or "")
        delta = qty if str(f.get("side")) == "buy" else -qty
        st = state.setdefault(key, {
            "symbol": sym, "pos": 0, "avg": 0.0, "entry_ts": 0,
            "fix_pts": 0.0, "fills": 0, "lots": 0, "comm": 0.0, "comm_known": True,
            "first_ms": int(f.get("ts_ms") or 0), "last_ms": 0,
        })
        # Одна заявка живёт в ОДНОМ инструменте; если вдруг нет — считаем по
        # первому и не смешиваем пункты разных шкал.
        if sym and st["symbol"] and sym != st["symbol"]:
            continue
        ts_ms = int(f.get("ts_ms") or 0)
        # Закрывает ли филл позицию заявки — до применения: после него знак уже другой.
        is_close = st["pos"] != 0 and (st["pos"] > 0) != (delta > 0)
        comm = fill_commission(sym or st["symbol"], price, qty,
                               float(point_values.get(sym or st["symbol"]) or 0),
                               is_close, st["entry_ts"], ts_ms)
        # Хоть у одного филла нет ₽/пункт — сумма комиссии неполна и числом не
        # выдаётся: заниженная комиссия делает чистый результат лучше, чем он есть.
        if comm is None:
            st["comm_known"] = False
        else:
            st["comm"] += comm
        pos, avg, ets, realized = apply_fill(
            st["pos"], st["avg"], st["entry_ts"], delta, price, ts_ms)
        st.update(pos=pos, avg=avg, entry_ts=ets)
        st["fix_pts"] += realized
        st["fills"] += 1
        st["lots"] += qty
        st["last_ms"] = int(f.get("ts_ms") or 0)

    out: dict[str, dict[str, Any]] = {}
    for key, st in state.items():
        sym = st["symbol"]
        pv = float(point_values.get(sym) or 0)
        last = float(last_prices.get(sym) or 0)
        # ВМ ТОЛЬКО У ЖИВОЙ ПОЗИЦИИ и только при известной цене. Нет цены —
        # переоценки нет, а не ноль: ноль читается как «в нуле».
        vm_pts = ((last - st["avg"]) * st["pos"]) if (st["pos"] and last > 0 and st["avg"]) else None
        fix_rub = round(st["fix_pts"] * pv, 2) if pv > 0 else None
        vm_rub = round(vm_pts * pv, 2) if (vm_pts is not None and pv > 0) else None
        comm_rub = round(st["comm"], 2) if (pv > 0 and st["comm_known"]) else None
        net_rub = (round(fix_rub - comm_rub, 2) if (fix_rub is not None and comm_rub is not None) else None)
        # Чистый результат С переоценкой: у живой позиции без известной цены ВМ
        # неизвестна, и тогда итога нет — «фикс минус комиссия» выдавал бы себя за
        # полный результат, в котором недостаёт целой ноги.
        if net_rub is None or (st["pos"] and vm_rub is None):
            total_rub = None
        else:
            total_rub = round(net_rub + (vm_rub if st["pos"] else 0.0), 2)
        out[key] = {
            "symbol": sym,
            "pos": st["pos"],
            "avg": round(st["avg"], 4) if st["avg"] else None,
            "fills": st["fills"],
            "lots": st["lots"],
            "fix_pts": round(st["fix_pts"], 4),
            "vm_pts": None if vm_pts is None else round(vm_pts, 4),
            "fix_rub": fix_rub,
            "vm_rub": vm_rub,
            "commission_rub": comm_rub,
            "net_rub": net_rub,
            "total_rub": total_rub,
            # Пункт не рубль. Нет ₽/пункт — экран обязан печатать пункты и слово
            # «п.», а не выдавать одно за другое.
            "priced": pv > 0,
            "first_ms": st["first_ms"],
            "last_ms": st["last_ms"],
        }
    return out
