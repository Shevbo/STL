import pytest

from robot_runner.bars import BarBuilder
from robot_runner.runtime import AgentRuntime


class FakeBridge:
    def __init__(self):
        self.placed = []

    async def place_order(self, **kw):
        self.placed.append(kw)


def _rt(max_position=1):
    return AgentRuntime("r1", FakeBridge(), BarBuilder(), max_position=max_position)


class U:  # minimal OrderUpdate stand-in (duck-typed accessors)
    def __init__(self, client_id, state, price, qty, side=1, order_id="12345"):
        self.client_id = client_id
        self.order_id = order_id
        self.code = "RIU6"
        self.side = side
        self.state = state
        self.price = price
        self.quantity = qty
        self.filled = qty
        self.text = ""
        self.ts_unix_ms = 0


@pytest.mark.asyncio
async def test_place_order_sends_and_position_updates_on_fill():
    rt = _rt()
    order = await rt.place_order("RIU6", "buy", 1, 89000.0)
    assert order.status == "submitted"
    assert len(rt._bridge.placed) == 1
    assert rt._bridge.placed[0]["code"] == "RIU6"
    assert rt.signed_position() == 0  # not filled yet

    rt.on_order_event(U(order.order_id, state=4, price=89000.0, qty=1))
    assert rt.signed_position() == 1
    assert rt.recent_fills()[-1]["status"] == "filled"
    # duplicate event must NOT double-apply
    rt.on_order_event(U(order.order_id, state=4, price=89000.0, qty=1))
    assert rt.signed_position() == 1


@pytest.mark.asyncio
async def test_foreign_robot_events_ignored():
    rt = _rt()
    rt.on_order_event(U("rr:OTHER:1:abc", state=4, price=100.0, qty=1))
    rt.on_order_event(U("human-1", state=4, price=100.0, qty=1))
    assert rt.signed_position() == 0


@pytest.mark.asyncio
async def test_max_position_pre_send_guard():
    rt = _rt(max_position=1)
    await rt.place_order("RIU6", "buy", 1, 89000.0)
    rt._apply_fill("buy", 1, 89000.0)          # pretend it filled
    order = await rt.place_order("RIU6", "buy", 1, 89100.0)  # would make +2
    assert order.status == "skipped"
    assert len(rt._bridge.placed) == 1  # second order NEVER reached the bridge
    # closing/reducing IS allowed at the cap
    order2 = await rt.place_order("RIU6", "sell", 1, 89200.0)
    assert order2.status == "submitted"


@pytest.mark.asyncio
async def test_realized_pnl_signed_space():
    rt = _rt(max_position=2)
    rt._apply_fill("buy", 1, 100.0)
    rt._apply_fill("sell", 1, 110.0)
    assert rt.realized_pnl() == pytest.approx(10.0)
    assert rt.signed_position() == 0
    # short side
    rt._apply_fill("sell", 2, 110.0)
    rt._apply_fill("buy", 2, 100.0)
    assert rt.realized_pnl() == pytest.approx(10.0 + 20.0)


@pytest.mark.asyncio
async def test_paper_mode_fills_instantly_no_bridge():
    rt = AgentRuntime("r1", FakeBridge(), BarBuilder(), max_position=1, paper=True)
    order = await rt.place_order("RIU6", "buy", 1, 89000.0)
    assert order.status == "paper"
    assert rt.signed_position() == 1
    assert len(rt._bridge.placed) == 0  # never touched the bridge


def test_restore_reseeds_position():
    rt = _rt()
    rt.restore(position=-1, avg=88000.0, realized=150.0)
    assert rt.signed_position() == -1
    assert rt.avg_price() == 88000.0
    assert rt.realized_pnl() == 150.0
