import pytest
from trader.lab.runtime import BacktestRuntime, Bar
from trader.lab.strategies.ema_crossover import on_bar as ema_on_bar
from trader.lab.strategies.rsi_mean_reversion import on_bar as rsi_on_bar


def make_runtime(prices: list[float]) -> BacktestRuntime:
    bars = [Bar(time=i*60, open=p, high=p+0.5, low=p-0.5, close=p, volume=1000)
            for i, p in enumerate(prices)]
    return BacktestRuntime(bars=bars, symbol="SIM6", initial_equity=100_000.0)


@pytest.mark.asyncio
async def test_ema_crossover_buys_on_uptrend():
    prices = [100.0] * 30 + [100.0 + i * 0.5 for i in range(30)]
    rt = make_runtime(prices)
    params = {"symbol": "SIM6", "fast_period": 5, "slow_period": 20}
    rt._cursor = 25
    for _ in range(20):
        await ema_on_bar(rt, params)
        rt.advance()
    orders = await rt.get_orders()
    buys = [o for o in orders if o.side == "buy"]
    assert len(buys) >= 1


@pytest.mark.asyncio
async def test_rsi_buys_on_oversold():
    prices = [100.0 - i * 0.8 for i in range(30)]
    rt = make_runtime(prices)
    params = {"symbol": "SIM6", "period": 14, "oversold": 30, "overbought": 70}
    rt._cursor = 20
    for _ in range(5):
        await rsi_on_bar(rt, params)
        rt.advance()
    orders = await rt.get_orders()
    buys = [o for o in orders if o.side == "buy"]
    assert len(buys) >= 1


def test_us_open_hm_day_offset():
    """bar_offset_min recovers MSK wall time from TRUE-UTC bars (agent runner);
    default 0 keeps the historic MSK-as-UTC backtest behaviour."""
    from datetime import datetime, timezone
    from trader.lab.strategies.us_open_fvg import _hm_day
    t = int(datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc).timestamp())
    assert _hm_day(t) == (13 * 60 + 30, 20260714)          # backtest bars: as-is
    assert _hm_day(t, 180) == (16 * 60 + 30, 20260714)     # runner bars: +3h -> 16:30 MSK


# ---- cooldown + superaverage generic modifiers on make_on_bar ----
from decimal import Decimal
from trader.lab.runtime import Bar as _Bar
from trader.pos.models import Position as _Position
from trader.lab.strategies.library import register as _register, make_on_bar as _mob, REGISTRY as _REG

_WANT = {"v": None}
if "_cdtest" not in _REG:
    _register("_cdtest", "test", "test", [], lambda bars, p: _WANT["v"], lambda p: 1)


class _FakeRT:
    def __init__(self):
        self._st = {}
        self.orders = []
        self.signed = 0
        self.avg = 0.0
        self.fills_enabled = True
        self._bars = []

    def push(self, t, price):
        self._bars.append(_Bar(time=t, open=price, high=price, low=price, close=price, volume=1))

    async def get_bars(self, sym, tf, n):
        return self._bars[-n:]

    async def get_position(self, sym):
        side = "long" if self.signed > 0 else ("short" if self.signed < 0 else "flat")
        return _Position(symbol=sym, account_id="t", side=side, quantity=abs(self.signed),
                         avg_price=Decimal(str(self.avg)), current_price=Decimal("0"),
                         var_margin=Decimal("0"))

    async def place_order(self, sym, side, qty, price):
        self.orders.append((side, qty, price))
        if not self.fills_enabled:      # живая заявка может простоять неисполненной
            return
        delta = qty if side == "buy" else -qty
        s, a = self.signed, self.avg
        newp = s + delta
        if newp == 0:
            self.signed, self.avg = 0, 0.0
        elif s != 0 and (s > 0) == (delta > 0):
            self.avg = (a * abs(s) + price * qty) / (abs(s) + qty)
            self.signed = newp
        elif s != 0 and (newp > 0) == (s > 0):
            self.signed = newp                      # partial reduce keeps avg
        else:
            self.signed, self.avg = newp, price

    def get_state(self, k, d=None):
        return self._st.get(k, d)

    def set_state(self, k, v):
        self._st[k] = v

    def log(self, msg, level="info"):
        pass


async def _run(rt, want, t, price, **params):
    _WANT["v"] = want
    rt.push(t, price)
    p = {"symbol": "SIM6", "qty": 1, "avg_max": 1, **params}
    await _mob("_cdtest")(rt, p)


@pytest.mark.asyncio
async def test_inv_suffix_fades_the_base_signal():
    """Контр-стратегия <id>__inv: та же логика, но сигнал инвертирован. Раньше
    жила только на i9 (без git) — контр-лидеры не открывались и не перебирались.
    Теперь make_on_bar('<id>__inv') работает: base=BUY -> inv=SELL."""
    # База: сигнал +1 (лонг) на разогретых барах -> покупка.
    base = _FakeRT()
    for i in range(3):
        _WANT["v"] = 1
        base.push(i, 100.0)
        await _mob("_cdtest")(base, {"symbol": "SIM6", "qty": 1, "avg_max": 1})
    assert base.signed > 0, "база должна открыть ЛОНГ на сигнале +1"

    # Контра: тот же сигнал +1, но __inv инвертирует -> шорт.
    inv = _FakeRT()
    for i in range(3):
        _WANT["v"] = 1
        inv.push(i, 100.0)
        await _mob("_cdtest__inv")(inv, {"symbol": "SIM6", "qty": 1, "avg_max": 1})
    assert inv.signed < 0, "контра должна открыть ШОРТ на том же сигнале +1"


@pytest.mark.asyncio
async def test_inv_leaves_flat_signal_flat():
    """Сигнал 0/None инвертировать нечего — контра тоже стоит вне рынка."""
    inv = _FakeRT()
    for i in range(3):
        _WANT["v"] = 0
        inv.push(i, 100.0)
        await _mob("_cdtest__inv")(inv, {"symbol": "SIM6", "qty": 1, "avg_max": 1})
    assert inv.signed == 0


@pytest.mark.asyncio
async def test_superaverage_escalates_qty_and_avgmax_on_loss_resets_on_win():
    rt = _FakeRT()
    sp = dict(super_y=1, super_z=3)
    await _run(rt, 1, 60, 100.0, **sp)      # flat -> open long 1 @100
    assert rt.orders[-1] == ("buy", 1, 100.0)
    await _run(rt, 0, 120, 98.0, **sp)      # flat signal -> exit at LOSS (reverses w/ cooldown off)
    assert rt.get_state("super_level") == 1
    await _run(rt, 1, 180, 98.0, **sp)      # next entry escalated: qty 1+1=2
    assert rt.orders[-1] == ("buy", 2, 98.0)
    await _run(rt, 0, 240, 96.0, **sp)      # another loss -> level 2
    assert rt.get_state("super_level") == 2
    await _run(rt, 1, 300, 96.0, **sp)      # qty 1+2=3
    assert rt.orders[-1] == ("buy", 3, 96.0)
    # a winning exit resets escalation to base
    await _run(rt, 0, 360, 200.0, **sp)     # exit far in profit -> win
    assert rt.get_state("super_level") == 0
    await _run(rt, 1, 420, 200.0, **sp)     # back to base qty 1
    assert rt.orders[-1] == ("buy", 1, 200.0)


@pytest.mark.asyncio
async def test_cooldown_blocks_entry_after_profit_and_reversal_is_exit_only():
    rt = _FakeRT()
    cd = dict(cooldown_min=10, cooldown_pct=1.0)   # 10 min pause after >1% winner
    await _run(rt, 1, 600, 100.0, **cd)     # open long 1 @100
    assert rt.signed == 1
    n_before = len(rt.orders)
    await _run(rt, -1, 660, 102.0, **cd)    # flip signal @ +2% -> EXIT ONLY (no short opened)
    assert rt.signed == 0                    # flat
    assert len(rt.orders) == n_before + 1    # exactly one order (the close), not a reversal
    assert rt.get_state("cooldown_until") == 660 + 600
    # within the cooldown window a fresh signal must NOT open
    await _run(rt, 1, 900, 102.0, **cd)      # 900 < 1260 -> blocked
    assert rt.signed == 0
    # after the window expires it opens again
    await _run(rt, 1, 1320, 102.0, **cd)     # 1320 > 1260 -> allowed
    assert rt.signed == 1


# ── «Разножка»: минимальная дистанция от прошлого входа (min_gap_pts) ─────────

@pytest.mark.asyncio
async def test_min_gap_blocks_reentry_within_gap_and_allows_beyond():
    rt = _FakeRT()
    g = dict(min_gap_pts=200)
    await _run(rt, 1, 60, 87000.0, **g)        # flat -> вход long @87000
    assert rt.orders[-1] == ("buy", 1, 87000.0)
    await _run(rt, 0, 120, 87050.0, **g)       # сигнал flat -> выход (никогда не блокируется)
    assert rt.orders[-1] == ("sell", 1, 87050.0)
    n = len(rt.orders)
    await _run(rt, 1, 180, 87100.0, **g)       # 100 пт от входа 87000 -> вход ЗАПРЕЩЁН
    assert len(rt.orders) == n
    await _run(rt, 1, 240, 87250.0, **g)       # 250 пт -> вход разрешён
    assert rt.orders[-1] == ("buy", 1, 87250.0)


@pytest.mark.asyncio
async def test_min_gap_never_blocks_exit_or_reversal_close():
    rt = _FakeRT()
    g = dict(min_gap_pts=200)
    await _run(rt, 1, 60, 87000.0, **g)        # вход long @87000
    await _run(rt, -1, 120, 87020.0, **g)      # разворот в 20 пт: ЗАКРЫТИЕ обязано пройти
    assert ("sell", 1, 87020.0) in rt.orders
    assert rt.signed == 0                       # открытие обратной ноги отсечено разножкой
    await _run(rt, -1, 180, 86700.0, **g)      # 300 пт от входа -> шорт открывается
    assert rt.orders[-1] == ("sell", 1, 86700.0)


@pytest.mark.asyncio
async def test_min_gap_off_by_default_keeps_old_behaviour():
    rt = _FakeRT()
    await _run(rt, 1, 60, 87000.0)             # без параметра фильтр выключен
    await _run(rt, 0, 120, 87010.0)
    await _run(rt, 1, 180, 87010.0)            # повторный вход вплотную -> разрешён
    assert rt.orders[-1] == ("buy", 1, 87010.0)


@pytest.mark.asyncio
async def test_min_gap_blocks_averaging_add_too():
    rt = _FakeRT()
    g = dict(min_gap_pts=200, avg_max=3, avg_step_atr=1, avg_atr_n=2)
    for i, p in enumerate([87000.0, 86900.0, 87000.0]):   # прогрев + ненулевой ATR
        await _run(rt, None, 60 + i * 60, p, **g)
    await _run(rt, 1, 300, 87000.0, **g)       # вход long @87000
    assert rt.orders[-1] == ("buy", 1, 87000.0)
    n = len(rt.orders)
    await _run(rt, None, 360, 86950.0, **g)    # против позиции, но всего 50 пт -> добора нет
    assert len(rt.orders) == n
    await _run(rt, None, 420, 86700.0, **g)    # 300 пт -> добор разрешён
    assert rt.orders[-1] == ("buy", 1, 86700.0)


@pytest.mark.asyncio
async def test_min_gap_ignores_an_order_that_never_filled():
    """Отсчёт разножки двигает только ИСПОЛНИВШИЙСЯ вход, не выставленная заявка."""
    rt = _FakeRT()
    g = dict(min_gap_pts=200)
    await _run(rt, 1, 60, 87000.0, **g)        # вход @87000 — исполнился
    assert rt.orders[-1] == ("buy", 1, 87000.0)
    await _run(rt, 0, 120, 87500.0, **g)       # выход (отсчёт подтверждён = 87000)
    assert rt.signed == 0

    rt.fills_enabled = False                    # следующая заявка «зависнет»
    await _run(rt, 1, 180, 86700.0, **g)       # 300 пт от 87000 -> заявка уходит
    assert rt.orders[-1] == ("buy", 1, 86700.0)
    assert rt.signed == 0                       # но НЕ исполнилась
    rt.fills_enabled = True

    n = len(rt.orders)
    await _run(rt, 1, 240, 86960.0, **g)       # 260 пт от НЕисполненной, но 40 от 87000
    assert len(rt.orders) == n                  # -> запрещено (старое поведение пускало)


def test_pivot_hlc_cache_is_keyed_by_symbol():
    """Кэш прошлого дня не должен подсовывать одному инструменту уровни другого."""
    from trader.lab.strategies.library import _PIVOT_HLC, sig_pivot
    _PIVOT_HLC.clear()

    def series(base: float, last_close: float):
        t = 1_700_000_000
        out = [_Bar(time=t + i * 60, open=base, high=base + 100, low=base - 100,
                    close=base, volume=1) for i in range(3)]
        out.append(_Bar(time=t + 86400, open=base, high=base, low=base,
                        close=last_close, volume=1))          # следующий день
        return out

    # день назад: H=base+100 L=base-100 C=base -> pivot=base, S1=base-100, R1=base+100
    a = series(100000.0, 99500.0)      # ушёл ниже S1 -> лонг
    b = series(50000.0, 50500.0)       # ушёл выше R1 -> шорт
    assert sig_pivot(a, {"symbol": "AAA", "level": 1}) == 1
    assert sig_pivot(b, {"symbol": "BBB", "level": 1}) == -1   # утёк бы кэш -> вернуло бы 1
    assert {k[0] for k in _PIVOT_HLC} == {"AAA", "BBB"}        # по записи на инструмент


@pytest.mark.asyncio
async def test_filter_skip_counters():
    """Разножка и остывание копят счётчик отсева, не меняя порядок заявок."""
    rt = _FakeRT()
    g = dict(min_gap_pts=200)
    await _run(rt, 1, 60, 87000.0, **g)        # вход @87000 (исполнился)
    await _run(rt, 0, 120, 87050.0, **g)       # выход -> отсчёт подтверждён = 87000
    n = len(rt.orders)
    await _run(rt, 1, 180, 87080.0, **g)       # 80 пт -> разножка отсеяла
    assert len(rt.orders) == n                  # заявки нет
    assert rt.get_state("gap_skips") == 1
    await _run(rt, 1, 240, 87120.0, **g)       # 120 пт -> снова отсеяла
    assert rt.get_state("gap_skips") == 2

    # остывание: отдельный счётчик
    rt2 = _FakeRT()
    cd = dict(cooldown_min=10, cooldown_pct=1.0)
    await _run(rt2, 1, 600, 100.0, **cd)       # вход
    await _run(rt2, 0, 660, 102.0, **cd)       # выход в +2% -> взвёл остывание
    await _run(rt2, 1, 900, 102.0, **cd)       # в окне остывания -> отсеяло
    assert rt2.get_state("cooldown_skips") == 1
    assert rt2.get_state("gap_skips") is None    # не перепутаны


@pytest.mark.asyncio
async def test_filter_effect_counts_saved_points():
    """Отсеянный вход дозревает через час: если он был бы убыточным — фильтр сберёг."""
    rt = _FakeRT()
    g = dict(min_gap_pts=200)
    await _run(rt, 1, 60, 87000.0, **g)          # вход @87000
    await _run(rt, 0, 120, 87050.0, **g)         # выход -> отсчёт = 87000
    await _run(rt, 1, 180, 87080.0, **g)         # 80 пт -> ОТСЕЯН (фантом buy @87080)
    assert rt.get_state("gap_skips") == 1
    assert len(rt.get_state("skip_phantoms")) == 1
    assert rt.get_state("filter_saved_pts") in (None, 0)   # ещё не дозрел

    # через час цена НИЖЕ -> фантомный лонг был бы в минусе -> фильтр сберёг
    await _run(rt, None, 180 + 3600, 86800.0, **g)
    saved = rt.get_state("filter_saved_pts")
    assert saved == pytest.approx(280.0)          # -(86800-87080)*1*1 = +280 пт сбережено
    assert rt.get_state("skip_phantoms") == []    # дозревший фантом закрыт


@pytest.mark.asyncio
async def test_filter_effect_negative_when_skip_would_have_won():
    """Если отсеянный вход был бы прибыльным — эффект отрицательный (недозаработали)."""
    rt = _FakeRT()
    g = dict(min_gap_pts=200)
    await _run(rt, 1, 60, 87000.0, **g)
    await _run(rt, 0, 120, 87050.0, **g)
    await _run(rt, 1, 180, 87080.0, **g)         # отсеян лонг @87080
    await _run(rt, None, 180 + 3600, 87400.0, **g)   # цена ВЫШЕ -> лонг был бы в плюсе
    assert rt.get_state("filter_saved_pts") == pytest.approx(-320.0)
