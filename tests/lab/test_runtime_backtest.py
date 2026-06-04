import pytest
from trader.lab.runtime import BacktestRuntime, Bar


@pytest.fixture
def bars():
    return [
        Bar(time=i * 60, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.5 + i, volume=1000)
        for i in range(20)
    ]


@pytest.fixture
def runtime(bars):
    return BacktestRuntime(bars=bars, symbol="TESTSYMBOL", initial_equity=100_000.0)


@pytest.mark.asyncio
async def test_get_bars_returns_slice(runtime):
    result = await runtime.get_bars("TESTSYMBOL", tf=1, n=5)
    assert len(result) == 5
    assert result[-1].close == pytest.approx(104.5)


@pytest.mark.asyncio
async def test_place_order_fills_next_bar(runtime, bars):
    runtime._cursor = 5
    order = await runtime.place_order("TESTSYMBOL", "buy", 1, bars[5].close)
    assert order.status == "filled"
    assert order.fill_price == pytest.approx(bars[6].open)


@pytest.mark.asyncio
async def test_get_position_after_buy(runtime, bars):
    runtime._cursor = 5
    await runtime.place_order("TESTSYMBOL", "buy", 2, bars[5].close)
    pos = await runtime.get_position("TESTSYMBOL")
    assert pos.side == "long"
    assert pos.quantity == 2


def test_state_get_set(runtime):
    runtime.set_state("key", 42)
    assert runtime.get_state("key") == 42
    assert runtime.get_state("missing", default="x") == "x"
