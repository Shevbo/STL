"""Вторая нога переворота не должна уходить, пока висит встречная заявка.

Переворот сигнала в make_on_bar — это ДВЕ заявки подряд: выход всей позицией и
следом вход в противоположную сторону. В бэктесте обе исполняются в одном баре,
вживую выход ещё стоит на рынке, когда приходит вход, и QUIK отвечает
«Обработка кросс-заявок блокирована» — вход теряется, робот остаётся в флэте
вместо разворота (4 таких отказа у живого lxk22tsffsxiiotb8kmpsato за июль).
"""
import asyncio


from robot_runner import runtime as rt_mod
from robot_runner.bars import BarBuilder
from robot_runner.runtime import AgentRuntime


class FakeBridge:
    def __init__(self):
        self.placed = []

    async def place_order(self, **kw):
        self.placed.append(kw)

    async def cancel_order(self, *a):
        pass


class U:  # minimal OrderUpdate stand-in (duck-typed accessors)
    def __init__(self, client_id, state, price, qty, side=1):
        self.client_id, self.state, self.price = client_id, state, price
        self.quantity = self.filled = qty
        self.side, self.code, self.order_id, self.text, self.ts_unix_ms = side, "RIU6", "1", "", 0


def _rt():
    return AgentRuntime("r1", FakeBridge(), BarBuilder(), max_position=10, paper=False)


async def test_entry_waits_for_the_exit_to_clear(monkeypatch):
    monkeypatch.setattr(rt_mod, "_CROSS_WAIT_SEC", 5.0)
    rt = _rt()
    rt.restore(position=2, avg=90000.0, realized=0.0)
    exit_order = await rt.place_order("RIU6", "sell", 2, 90100.0)   # нога 1: выход

    entry = asyncio.ensure_future(rt.place_order("RIU6", "buy", 2, 90100.0))  # нога 2
    await asyncio.sleep(0.35)
    assert len(rt._bridge.placed) == 1, "вход ушёл, пока выход ещё висел на рынке"

    rt.on_order_event(U(exit_order.order_id, state=rt_mod._FILLED_STATE,
                        price=90100.0, qty=2, side=2))
    await asyncio.wait_for(entry, timeout=2)
    assert len(rt._bridge.placed) == 2                # вход ушёл сразу после филла
    assert rt._bridge.placed[-1]["side"] == "buy"


async def test_wait_gives_up_and_sends_anyway(monkeypatch):
    """Пропустить вход хуже, чем рискнуть отказом: так теряется весь переворот."""
    monkeypatch.setattr(rt_mod, "_CROSS_WAIT_SEC", 0.4)
    rt = _rt()
    rt.restore(position=2, avg=90000.0, realized=0.0)
    await rt.place_order("RIU6", "sell", 2, 90100.0)
    await rt.place_order("RIU6", "buy", 2, 90100.0)   # встречная так и висит
    assert len(rt._bridge.placed) == 2


async def test_same_side_order_never_waits(monkeypatch):
    """Усреднение в ту же сторону кросс-заявкой не бьётся — ждать нечего."""
    monkeypatch.setattr(rt_mod, "_CROSS_WAIT_SEC", 30.0)
    rt = _rt()
    await rt.place_order("RIU6", "buy", 2, 90000.0)
    await asyncio.wait_for(rt.place_order("RIU6", "buy", 2, 89900.0), timeout=2)
    assert len(rt._bridge.placed) == 2
