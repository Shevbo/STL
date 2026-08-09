"""1-minute bar aggregation from local QUIK DDE ticks.

Strategies consume CLOSED bars only (the forming minute is invisible) so a
signal computed mid-minute cannot repaint — matching backtest semantics where
on_bar sees completed candles.
"""

from collections import deque

from trader.lab.runtime import Bar


def pick_price(last: float, bid: float, ask: float) -> float:
    """Bar price from a tick: trade price when the feed carries it, else the
    mid, else whichever side exists. The QUIK DDE export may lack the
    last-price column (params sheet has Спрос/Предл. only) — without this
    fallback no bars would ever build. Returns 0.0 when nothing usable."""
    if last > 0:
        return last
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return bid if bid > 0 else (ask if ask > 0 else 0.0)


class BarBuilder:
    # After a tape trade arrives, snapshot ticks are ignored for this long:
    # the tape is exact (every exchange trade, true volume); mixing in bid/ask
    # snapshots would corrupt OHLC. Expires so a tape outage falls back to ticks.
    TAPE_PRIORITY_MS = 30_000

    def __init__(self, max_bars: int = 3000) -> None:
        self._bars: deque[Bar] = deque(maxlen=max_bars)
        self._cur: Bar | None = None   # forming minute
        self._last_trade_ms = 0        # newest tape trade seen (priority window)

    def _merge(self, ts_ms: int, price: float, volume: int, traded: bool) -> None:
        minute = int(ts_ms // 60_000) * 60  # unix seconds
        if self._bars and minute <= self._bars[-1].time:
            return  # at/behind the newest CLOSED bar (incl. a restored tail) — ignore
        cur = self._cur
        if cur is None or minute > cur.time:
            if cur is not None:
                self._bars.append(cur)   # close the previous minute
            self._cur = Bar(time=minute, open=price, high=price, low=price,
                            close=price, volume=volume, traded=traded)
            return
        if minute < cur.time:
            return  # late data from a closed minute — ignore
        cur.high = max(cur.high, price)
        cur.low = min(cur.low, price)
        cur.close = price
        cur.volume += volume
        # Метка «в баре была реальная сделка» ТОЛЬКО взводится: минута, начатая
        # котировкой и продолженная сделкой, — настоящая; обратно снимать её
        # нельзя, иначе поздний тик стёр бы факт сделки.
        cur.traded = cur.traded or traded

    def on_tick(self, ts_ms: int, last: float) -> None:
        """Snapshot price (bid/ask mid or last param). Fallback source: muted
        while exact tape trades are flowing."""
        if last <= 0:
            return
        if self._last_trade_ms and ts_ms - self._last_trade_ms < self.TAPE_PRIORITY_MS:
            return
        self._merge(ts_ms, last, 0, traded=False)

    def on_trade(self, ts_ms: int, price: float, qty: int) -> None:
        """Exact exchange trade from the anonymized tape (OnAllTrade)."""
        if price <= 0:
            return
        self._last_trade_ms = max(self._last_trade_ms, ts_ms)
        self._merge(ts_ms, price, max(0, qty), traded=True)

    def bars(self, n: int = 0) -> list[Bar]:
        out = list(self._bars)
        return out[-n:] if n else out

    def traded_bars(self, n: int = 0) -> list[Bar]:
        """Только бары с РЕАЛЬНЫМИ сделками — для показа человеку.

        Стратегия читает `bars()` и синтетические минуты видит как раньше:
        менять её вход значило бы менять торговлю. А нарисованная свеча на
        стоящей бирже — это ложь на экране (панель всю ночь рисовала минутки
        по замершей котировке), поэтому наружу отдаём только факт биржи."""
        out = [b for b in self._bars if b.traded]
        return out[-n:] if n else out

    # ---- persistence (restart immunity) ----
    # Bars used to live only in memory: every runner/agent restart blinded a
    # long-lookback robot for `lookback` minutes (order_block: ~2h; us_open:
    # a whole day's setup if restarted near the open). Closed bars round-trip
    # through runner_state.json as compact rows.

    def to_rows(self, n: int = 600) -> list[list]:
        """Last n CLOSED bars as [t, o, h, l, c, v, traded] rows (the forming
        minute is not persisted — it rebuilds from the live tape in under a
        minute). Седьмая колонка ДОПИСАНА к историческому формату: старый
        раннер читает строку по индексам 0..5 и лишнюю просто не видит, так что
        откат версии безопасен."""
        return [[b.time, b.open, b.high, b.low, b.close, b.volume, int(b.traded)]
                for b in self.bars(n)]

    def seed(self, rows: list) -> None:
        """Restore persisted rows into an EMPTY builder (fresh deploy after a
        restart). Malformed rows are skipped — a corrupt state file must never
        block trading; the builder just warms up live instead."""
        if self._bars or self._cur is not None:
            return  # live data already flowing: never overwrite reality
        for r in rows or []:
            try:
                t, o, h, lo, c, v = int(r[0]), float(r[1]), float(r[2]), \
                    float(r[3]), float(r[4]), int(r[5])
            except (TypeError, ValueError, IndexError):
                continue
            if self._bars and t <= self._bars[-1].time:
                continue  # keep strictly ascending
            # Строка ДО этой версии (6 колонок) — считаем бар настоящим: иначе
            # первый же рестарт на старом state спрятал бы у панели всю историю
            # разом, а промах в эту сторону всего лишь оставит на сутки
            # несколько ночных минут (их дорисовку страхует и сама панель).
            traded = bool(r[6]) if len(r) > 6 else True
            self._bars.append(Bar(time=t, open=o, high=h, low=lo, close=c, volume=v,
                                  traded=traded))

    @property
    def last_bar_time(self) -> int:
        return self._bars[-1].time if self._bars else 0
