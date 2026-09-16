"""Исполнение бэктеста ПО АРХИВУ СТАКАНА вместо открытия следующего бара (16.09.2026).

ЗАЧЕМ. Обычный BacktestRuntime исполняет заявку по open следующего бара: спреда нет,
глубины нет, проскальзывания нет. Архив сырого рынка (docs/market-archive.md,
/home/ubuntu/market-archive на хостере) с 12.08.2026 пишет стакан RIU6 на 10 уровней.
Этого мало для ОТБОРА стратегий (36 дней), но достаточно, чтобы ИЗМЕРИТЬ, насколько
врёт исполнение по бару.

ВРЕМЯ. Бары ISS — московская стенка, проставленная как UTC; в архиве настоящий UTC
(received_at_unix_ms от QUIK). Поэтому метка стакана приводится к шкале баров
сдвигом +3 ч (MSK_SHIFT), иначе заявка исполнится по стакану трёхчасовой давности.

ГЛУБИНА. Рыночная заявка идёт по уровням встречной стороны: VWAP по съеденным
уровням. Если объёма в стакане не хватает, остаток исполняется по худшему уровню и
считается в `deep` — это ЗАНИЖЕННАЯ оценка проскальзывания, у нас всего 10 уровней.
"""
from __future__ import annotations

import bisect
import gzip
import json
import os

from trader.lab.runtime import BacktestRuntime

MSK_SHIFT = 3 * 3600


def load_book(paths: list[str], code: str) -> tuple[list[int], list[tuple]]:
    """Файлы архива -> (времена в шкале баров, книги). Книга = (bids, asks), уровни
    (цена, объём), bids по убыванию цены, asks по возрастанию."""
    times: list[int] = []
    books: list[tuple] = []
    for p in sorted(paths):
        op = gzip.open if p.endswith(".gz") else open
        with op(p, "rt", encoding="utf-8") as f:
            for ln in f:
                if code not in ln:
                    continue                      # дешёвый отсев; точная проверка ниже
                try:
                    r = json.loads(ln)
                    if r.get("code") != code:     # не по подстроке: форматирование JSON разное
                        continue
                    ts = int(r["received_at_unix_ms"]) // 1000 + MSK_SHIFT
                    bids = [(float(x["price"]), int(x["quantity"])) for x in r.get("bids") or []]
                    asks = [(float(x["price"]), int(x["quantity"])) for x in r.get("asks") or []]
                except (KeyError, TypeError, ValueError):
                    continue
                if not bids or not asks:
                    continue
                times.append(ts)
                books.append((sorted(bids, reverse=True), sorted(asks)))
    order = sorted(range(len(times)), key=times.__getitem__)
    return [times[i] for i in order], [books[i] for i in order]


def load_dir(root: str, code: str) -> tuple[list[int], list[tuple]]:
    files = [os.path.join(root, f) for f in os.listdir(root) if f.startswith("book-")]
    return load_book(files, code)


class BookRuntime(BacktestRuntime):
    """BacktestRuntime, исполняющий рыночные заявки по архивному стакану.

    Берётся ПЕРВЫЙ снимок не раньше открытия следующего бара — то же событие, что
    исполняет обычный рантайм, но по реальной встречной стороне. Снимка нет (дыра в
    архиве, ночь, рестарт STL) — падаем на поведение базового класса, и такие филлы
    считаются в stats['no_book']: смешивать их с измерением нельзя.
    """

    def __init__(self, *a, book: tuple[list[int], list[tuple]] | None = None,
                 max_gap_s: int = 60, **kw) -> None:
        super().__init__(*a, **kw)
        self._bt, self._bb = book or ([], [])
        # ПОРОГ СВЕЖЕСТИ. В архиве есть провалы (рестарт STL, молчание агента): без
        # порога заявка находила «ближайший» снимок трёхсуточной давности и замер мерил
        # ход рынка за трое суток, а не спред. Дальше порога = стакана нет.
        self._max_gap = max_gap_s
        # slip = spread + drift: спред считается от середины ТОГО ЖЕ снимка, снос —
        # разница середины снимка и open бара. Без разделения редко торгующая стратегия
        # показывает «исполнение лучше бара»: это не спред, а уход цены за gap секунд.
        self.stats = {"book": 0, "no_book": 0, "deep": 0, "slip_pts": 0.0,
                      "spread_pts": 0.0, "drift_pts": 0.0, "gap_s": 0.0, "gap_max_s": 0}

    def _snapshot(self, ts: int):
        i = bisect.bisect_left(self._bt, ts)
        if i >= len(self._bt) or self._bt[i] - ts > self._max_gap:
            return None
        return self._bb[i], self._bt[i] - ts

    @staticmethod
    def _walk(levels: list[tuple], qty: int) -> tuple[float, bool]:
        """VWAP по уровням; True = глубины не хватило (остаток по худшему уровню)."""
        left, cost = qty, 0.0
        for price, vol in levels:
            take = min(left, vol)
            cost += take * price
            left -= take
            if left == 0:
                return cost / qty, False
        cost += left * levels[-1][0]
        return cost / qty, True

    async def get_orderbook(self, symbol: str):
        got = self._snapshot(self._bars[self._cursor].time)
        if got is None:
            return {"bids": [], "asks": []}
        (bids, asks), _gap = got
        return {"bids": [{"price": p, "quantity": q} for p, q in bids],
                "asks": [{"price": p, "quantity": q} for p, q in asks]}

    async def place_order(self, symbol: str, side: str, qty: int, price: float):
        nxt = self._bars[self._cursor + 1]
        got = self._snapshot(nxt.time)
        if got is None:
            self.stats["no_book"] += 1
            return await super().place_order(symbol, side, qty, price)
        (bids, asks), gap = got
        fill, deep = self._walk(asks if side == "buy" else bids, qty)
        mid = (bids[0][0] + asks[0][0]) / 2
        sign = 1 if side == "buy" else -1              # знак «хуже для нас»
        self.stats["book"] += 1
        self.stats["deep"] += deep
        self.stats["slip_pts"] += sign * (fill - nxt.open)
        self.stats["spread_pts"] += sign * (fill - mid)
        self.stats["drift_pts"] += sign * (mid - nxt.open)
        self.stats["gap_s"] += gap
        self.stats["gap_max_s"] = max(self.stats["gap_max_s"], gap)
        return self._apply_fill(symbol, side, qty, fill, nxt.time)
