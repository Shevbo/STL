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
from uuid import uuid4

from trader.lab.runtime import BacktestRuntime, Order

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


def load_digest(rows: list[list], levels: int = 5,
                ts_unit: str = "s") -> tuple[list[int], list[tuple]]:
    """Выжимка стакана (scripts/book_digest.py) -> тот же вид, что даёт load_book.

    Сырой архив весит сотню мегабайт и лежит только на хостере; i9 получает вместо
    него один снимок на минуту в файле `agent_bars/book<КОД>.json`. Строка:
    [ts, bid1,qty1 ... bid5,qty5, ask1,qty1 ... ask5,qty5], время УЖЕ в шкале баров
    (сдвиг +3 ч сделан при сборке), поэтому здесь ничего не сдвигаем.

    ts_unit="ms" — выжимка на полной частоте (scripts/book_full_digest.py, ключи
    book<КОД>f<ММДД>, ~1 снимок/1.2 с): метка переводится в ДРОБНЫЕ секунды.
    BookRuntime работает в секундах через bisect и арифметику, дроби ему не мешают,
    а округление до секунды сливало бы соседние снимки и сдвигало quote_lag_s.
    """
    times: list[int] = []
    books: list[tuple] = []
    ms = ts_unit == "ms"
    for r in rows:
        ts = r[0] / 1000 if ms else int(r[0])
        vals = r[1:]
        bids = [(float(vals[2 * i]), int(vals[2 * i + 1]))
                for i in range(levels) if 2 * i + 1 < len(vals)]
        off = 2 * levels
        asks = [(float(vals[off + 2 * i]), int(vals[off + 2 * i + 1]))
                for i in range(levels) if off + 2 * i + 1 < len(vals)]
        if not bids or not asks:
            continue
        times.append(ts)
        books.append((sorted(bids, reverse=True), sorted(asks)))
    return times, books


class BookRuntime(BacktestRuntime):
    """BacktestRuntime, исполняющий рыночные заявки по архивному стакану.

    Берётся ПЕРВЫЙ снимок не раньше открытия следующего бара — то же событие, что
    исполняет обычный рантайм, но по реальной встречной стороне. Снимка нет (дыра в
    архиве, ночь, рестарт STL) — падаем на поведение базового класса, и такие филлы
    считаются в stats['no_book']: смешивать их с измерением нельзя.

    РЕЖИМ ИСПОЛНЕНИЯ exec_mode (01.10, docs/execution-cost-program.md):
      "walk" (умолчание) — рыночная заявка проходит стакан на весь объём (VWAP).
        На 516 якорях lxk22 (RIU6) завышает: 15.4 пт/лот на 11+ при факте 6.55.
      "touch" — как живой раннер (robot_runner/runtime.py:165-177): лимит по
        встречному best. Сразу исполняется min(qty, объём на best) по best,
        остаток встаёт в очередь отложенных лимиток по той же цене. Доливка в
        advance() по снимкам между моментом заявки и следующим баром, правило
        touch_fill: "opt" — встречный best не хуже нашей цены, долив = видимый
        объём на уровнях не хуже нашей цены; "pess" — best прошёл нашу цену
        насквозь, долив всего остатка. Окно доливки = min(touch_ttl_s, до
        следующего on_bar): вживую host.py:476-490 снимает рабочие заявки перед
        КАЖДЫМ баром, поэтому остаток дольше бара не живёт ни там, ни здесь (и
        стратегия не настакивает повторы поверх висящего остатка).
        Не долилось: touch_ttl_action="cancel" (умолчание) — остаток снят,
        стратегия на следующем баре видит фактическую позицию и сама решает,
        переслать ли заявку (ровно как вживую); "market" — остаток добивается
        проходом стакана по снимку конца окна, позиция всегда сходится с
        намерением (для стратегий, которые ведут свою позицию в state и не
        переживают недолив).
        Выбран "cancel": движок бэктеста (backtest.py) берёт позицию из
        _positions и сделки из get_orders(), мгновенного полного исполнения он не
        требует; стратегии библиотеки вживую уже живут с недоливом, и бэктест с
        "market" был бы не той же механикой, что раннер.
        quote_lag_s (умолчание 0): цена лимита = встречный best снимка на
        t - lag (котировка раннера отстаёт от стакана, на якорях только 72% заявок
        маркетабельны). В t заявка встречает текущий стакан: сразу берёт уровни не
        хуже лимита (по их ценам), остаток стоит по лимиту; не пересекает — стоит
        целиком, place_order вернёт Order со status="submitted" без филла.
        Ограничения: stats spread/slip/drift считаются только по немедленной части; доливки и добор в stats["touch_*"]. Остаток на последнем баре
        прогона не доливается (advance() вернул False — дальше снимков не берём).
    """

    def __init__(self, *a, book: tuple[list[int], list[tuple]] | None = None,
                 max_gap_s: int = 60, exec_mode: str = "walk", touch_fill: str = "opt",
                 touch_ttl_s: int = 60, touch_ttl_action: str = "cancel",
                 quote_lag_s: float = 0, **kw) -> None:
        super().__init__(*a, **kw)
        if exec_mode not in ("walk", "touch") or touch_fill not in ("opt", "pess") \
                or touch_ttl_action not in ("cancel", "market"):
            raise ValueError(f"exec_mode={exec_mode} touch_fill={touch_fill} "
                             f"touch_ttl_action={touch_ttl_action}")
        self._exec_mode, self._touch_fill = exec_mode, touch_fill
        self._touch_ttl, self._touch_action = touch_ttl_s, touch_ttl_action
        self._quote_lag = quote_lag_s
        self._pending: list[dict] = []
        self._bt, self._bb = book or ([], [])
        # ПОРОГ СВЕЖЕСТИ. В архиве есть провалы (рестарт STL, молчание агента): без
        # порога заявка находила «ближайший» снимок трёхсуточной давности и замер мерил
        # ход рынка за трое суток, а не спред. Дальше порога = стакана нет.
        self._max_gap = max_gap_s
        # slip = spread + drift: спред считается от середины ТОГО ЖЕ снимка, снос —
        # разница середины снимка и open бара. Без разделения редко торгующая стратегия
        # показывает «исполнение лучше бара»: это не спред, а уход цены за gap секунд.
        self.stats = {"book": 0, "no_book": 0, "deep": 0, "slip_pts": 0.0,
                      "spread_pts": 0.0, "drift_pts": 0.0, "gap_s": 0.0, "gap_max_s": 0,
                      # СРЕДНЕЕ ПО СПРЕДУ ВРЁТ: медиана полспреда RIU6 = 5 пт, а среднее
                      # 7.8 — хвост делают ночь и предоткрытие (03:00 медиана 180 пт).
                      # Поэтому храним пофилловые значения и час МСК каждого филла.
                      "spread_each": [], "hour_each": [], "drift_each": [],
                      # exec_mode="touch": лотов встало остатком / долито по лимиту /
                      # снято / добито рынком.
                      "touch_rest": 0, "touch_late": 0, "touch_unfilled": 0, "touch_market": 0,
                      "touch_no_lag": 0}

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
        levels = asks if side == "buy" else bids
        rest = 0
        sign = 1 if side == "buy" else -1              # знак «хуже для нас»
        if self._exec_mode == "touch":
            t0 = nxt.time + gap                        # время снимка исполнения
            limit = self._lagged_best(t0, side, levels[0][0])
            took, value = 0, 0.0                       # сразу: уровни не хуже лимита
            for p, q in levels:
                if sign * (p - limit) > 1e-9 or took >= qty:
                    break
                take = min(qty - took, q)
                took, value = took + take, value + take * p
            rest = qty - took
            if rest:
                self._pending.append({"symbol": symbol, "side": side, "left": rest,
                                      "price": limit, "t0": t0,
                                      "deadline": t0 + self._touch_ttl})
                self.stats["touch_rest"] += rest
            if not took:                               # встала целиком, сразу ничего
                return Order(order_id=uuid4().hex[:12], symbol=symbol, side=side, qty=qty,
                             price=limit, status="submitted")
            qty, fill, deep = took, value / took, False
        else:
            fill, deep = self._walk(levels, qty)
        mid = (bids[0][0] + asks[0][0]) / 2
        self.stats["book"] += 1
        self.stats["deep"] += deep
        self.stats["slip_pts"] += sign * (fill - nxt.open)
        self.stats["spread_pts"] += sign * (fill - mid)
        self.stats["drift_pts"] += sign * (mid - nxt.open)
        self.stats["gap_s"] += gap
        self.stats["spread_each"].append(sign * (fill - mid))
        self.stats["hour_each"].append(nxt.time % 86400 // 3600)   # бары в шкале МСК
        self.stats["drift_each"].append(sign * (mid - nxt.open))
        self.stats["gap_max_s"] = max(self.stats["gap_max_s"], gap)
        return self._apply_fill(symbol, side, qty, fill, nxt.time)

    def _lagged_best(self, t0: int, side: str, best_now: float) -> float:
        """Встречный best последнего снимка НЕ ПОЗЖЕ t0 - quote_lag_s (котировка
        раннера отстаёт от стакана). Нет такого снимка в пределах max_gap_s —
        берём текущий best и считаем в stats["touch_no_lag"]."""
        if self._quote_lag <= 0:
            return best_now
        k = bisect.bisect_right(self._bt, t0 - self._quote_lag) - 1
        if k < 0 or t0 - self._quote_lag - self._bt[k] > self._max_gap:
            self.stats["touch_no_lag"] += 1
            return best_now
        bids, asks = self._bb[k]
        return (asks if side == "buy" else bids)[0][0]

    def advance(self) -> bool:
        ok = super().advance()
        if ok and self._pending:
            self._drain(self._bars[self._cursor + 1].time)
        return ok

    def _drain(self, now: int) -> None:
        """Доливка отложенных лимиток по снимкам (t0, min(deadline, now)], затем
        остаток снимается или добивается рынком (см. докстринг класса)."""
        for o in self._pending:
            buy = o["side"] == "buy"
            price = o["price"]
            end = min(o["deadline"], now)
            lo = bisect.bisect_right(self._bt, o["t0"])
            hi = bisect.bisect_right(self._bt, end)
            for j in range(lo, hi):
                if o["left"] == 0:
                    break
                bids, asks = self._bb[j]
                levels = asks if buy else bids
                best = levels[0][0]
                if self._touch_fill == "pess":
                    take = o["left"] if (best < price if buy else best > price) else 0
                elif best <= price if buy else best >= price:
                    take = min(o["left"], sum(q for p, q in levels if (p <= price if buy else p >= price)))
                else:
                    take = 0
                if take:
                    o["left"] -= take
                    self.stats["touch_late"] += take
                    # int: fill_time у Order целые секунды; у мс-выжимки метка дробная
                    self._apply_fill(o["symbol"], o["side"], take, price,
                                     int(self._bt[j]))
            if not o["left"]:
                continue
            if self._touch_action == "cancel":
                self.stats["touch_unfilled"] += o["left"]
                continue
            self.stats["touch_market"] += o["left"]
            got = self._snapshot(end)
            if got is None:
                self.stats["no_book"] += 1
                px = self._bars[self._cursor].open
            else:
                (bids, asks), _gap = got
                px, _deep = self._walk(asks if buy else bids, o["left"])
            self._apply_fill(o["symbol"], o["side"], o["left"], px, int(end))
        self._pending = []
