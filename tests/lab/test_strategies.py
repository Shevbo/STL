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


async def _run(rt, want, t, price, **params):
    _WANT["v"] = want
    rt.push(t, price)
    p = {"symbol": "SIM6", "qty": 1, "avg_max": 1, **params}
    await _mob("_cdtest")(rt, p)


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
