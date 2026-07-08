import json

import pytest

from robot_runner.host import RobotHost


class FakeBridge:
    def __init__(self):
        self.placed = []
        self.cancelled = []
        self.reports = []

    async def place_order(self, **kw):
        self.placed.append(kw)

    async def cancel_order(self, client_id, order_id):
        self.cancelled.append(client_id)

    async def report_status(self, r):
        self.reports.append(r)


def _deploy_rc(robot_id="r1", strategy="fvg", paper=True):
    """Duck-typed RunnerControl stand-in for handle_control."""
    class Spec:
        pass
    spec = Spec()
    spec.robot_id = robot_id
    spec.strategy_id = strategy
    spec.params_json = json.dumps({"symbol": "RIU6", "qty": 1, "min_frac": 12,
                                   "tp_atr": 60, "avg_max": 1, "avg_atr_n": 5,
                                   "avg_step_atr": 24})
    spec.symbol = "RIU6"
    spec.schedule = "00:00-23:59"
    spec.max_position_contracts = 1
    spec.paper = paper

    class Deploy:
        pass
    d = Deploy()
    d.spec = spec

    class RC:
        deploy = d

        def WhichOneof(self, _):
            return "deploy"
    return RC()


@pytest.mark.asyncio
async def test_deploy_creates_robot_and_persists(tmp_path):
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    assert "r1" in host.robots
    assert host.robots["r1"].spec["strategy_id"] == "fvg"
    assert (tmp_path / "runner_state.json").exists()

    # a second host instance resumes the robot's saved runtime state
    host.robots["r1"].runtime.set_state("trend", "up")
    host.robots["r1"].runtime.restore(position=1, avg=89000.0, realized=50.0)
    host.persist()
    host.robots["r1"].runtime._apply_fill("buy", 1, 89000.0, symbol="RIU6")
    host.persist()
    host2 = RobotHost(FakeBridge(), str(tmp_path))
    await host2.handle_control(_deploy_rc())
    r2 = host2.robots["r1"]
    assert r2.runtime.get_state("trend") == "up"
    assert r2.runtime.signed_position() == 2   # restored 1 + the extra buy fill
    assert r2.runtime.realized_pnl() == 50.0
    # the fill HISTORY survives the restart too (operator audit trail)
    fills = r2.runtime.recent_fills()
    assert fills and fills[-1]["price"] == 89000.0


@pytest.mark.asyncio
async def test_tick_runs_strategy_once_per_closed_bar(tmp_path):
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    r = host.robots["r1"]
    t0 = 1_751_500_000_000
    # feed enough 1-min closed bars for FVG warmup
    for i in range(10):
        r.bars.on_tick(t0 + i * 60_000, 89_000 + i * 30)
    ran = await host.tick_robot(r)   # True when on_bar executed
    assert ran is True
    assert r.last_bar_run == r.bars.last_bar_time
    assert r.last_error == ""        # strategy ran clean (warmup path included)
    # same bar -> no rerun (one on_bar per closed bar, backtest parity)
    assert await host.tick_robot(r) is False
    # new closed bar -> runs again
    r.bars.on_tick(t0 + 10 * 60_000, 89_400)
    r.bars.on_tick(t0 + 11 * 60_000, 89_500)
    assert await host.tick_robot(r) is True


@pytest.mark.asyncio
async def test_real_robot_cancels_resting_orders_before_next_bar(tmp_path):
    # A REAL limit order rests unfilled between bars. Without clearing it the
    # strategy re-emits its intent each bar and stacks duplicate real orders
    # (seen live: 8 resting BUYs at max_position=1). The host must cancel this
    # robot's working orders before re-running on_bar.
    bridge = FakeBridge()
    host = RobotHost(bridge, str(tmp_path))
    await host.handle_control(_deploy_rc(paper=False))
    r = host.robots["r1"]
    t0 = 1_751_500_000_000
    for i in range(10):
        r.bars.on_tick(t0 + i * 60_000, 89_000 + i * 30)
    assert await host.tick_robot(r) is True

    # simulate a real order that was placed and is still RESTING (submitted)
    o = await r.runtime.place_order("RIU6", "buy", 1, 89_000.0)
    assert o.status == "submitted"
    assert len(r.runtime.working_orders()) == 1
    resting_cid = o.order_id
    bridge.cancelled.clear()

    # a NEW closed bar -> the resting order is cancelled before on_bar runs
    r.bars.on_tick(t0 + 10 * 60_000, 89_400)
    r.bars.on_tick(t0 + 11 * 60_000, 89_500)
    assert await host.tick_robot(r) is True
    assert resting_cid in bridge.cancelled


@pytest.mark.asyncio
async def test_failed_precancel_expires_phantom_locally(tmp_path):
    # An order the agent cannot cancel (unknown after an agent restart / QUIK
    # day-expiry) must be terminated LOCALLY, or the runner's book shows
    # phantom orders forever (seen live: 8 non-existent BUYs).
    bridge = FakeBridge()
    host = RobotHost(bridge, str(tmp_path))
    await host.handle_control(_deploy_rc(paper=False))
    r = host.robots["r1"]
    t0 = 1_751_500_000_000
    for i in range(10):
        r.bars.on_tick(t0 + i * 60_000, 89_000 + i * 30)
    await host.tick_robot(r)
    o = await r.runtime.place_order("RIU6", "buy", 1, 89_000.0)
    assert len(r.runtime.working_orders()) == 1

    async def boom(client_id, order_id):
        raise RuntimeError("unknown order")
    bridge.cancel_order = boom

    r.bars.on_tick(t0 + 10 * 60_000, 89_400)
    r.bars.on_tick(t0 + 11 * 60_000, 89_500)
    assert await host.tick_robot(r) is True
    assert r.runtime.working_orders() == []      # phantom gone from the book
    assert r.runtime._orders[o.order_id].status == "expired"


@pytest.mark.asyncio
async def test_paper_tick_never_cancels(tmp_path):
    # Paper fills are instant -> no resting orders -> no cancel churn.
    bridge = FakeBridge()
    host = RobotHost(bridge, str(tmp_path))
    await host.handle_control(_deploy_rc(paper=True))
    r = host.robots["r1"]
    t0 = 1_751_500_000_000
    for i in range(12):
        r.bars.on_tick(t0 + i * 60_000, 89_000 + i * 30)
    await host.tick_robot(r)
    assert bridge.cancelled == []


@pytest.mark.asyncio
async def test_real_order_prices_marketable_from_host_quote(tmp_path):
    # Host feeds quotes; a REAL BUY goes out at the ASK (marketable), not at
    # the strategy's bar-close price, so it fills like the backtest assumes.
    bridge = FakeBridge()
    host = RobotHost(bridge, str(tmp_path))
    await host.handle_control(_deploy_rc(paper=False))
    r = host.robots["r1"]
    import time as _t
    now_ms = int(_t.time() * 1000)
    host.quotes["RIU6"] = (88_990.0, 89_010.0, now_ms)   # bid, ask, fresh
    o = await r.runtime.place_order("RIU6", "buy", 1, 88_900.0)
    assert bridge.placed[-1]["price"] == 89_010.0         # ask, not close
    assert o.price == 89_010.0
    s = await r.runtime.place_order("RIU6", "sell", 1, 89_100.0)
    assert bridge.placed[-1]["price"] == 88_990.0         # bid
    assert s.price == 88_990.0

    # STALE quote -> falls back to the strategy price
    host.quotes["RIU6"] = (88_990.0, 89_010.0, now_ms - 60_000)
    f = await r.runtime.place_order("RIU6", "sell", 1, 89_100.0)
    assert bridge.placed[-1]["price"] == 89_100.0
    assert f.price == 89_100.0


@pytest.mark.asyncio
async def test_kill_switch_pauses_everything_and_start_clears(tmp_path):
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    r = host.robots["r1"]
    r.bars.on_tick(1_751_500_000_000, 89_000)
    r.bars.on_tick(1_751_500_060_000, 89_100)

    class Kill:
        reason = "test"

    class KillRC:
        kill = Kill()

        def WhichOneof(self, _):
            return "kill"
    await host.handle_control(KillRC())
    assert host.killed is True
    assert await host.tick_robot(r) is False

    class Start:
        robot_id = "r1"

    class StartRC:
        start = Start()

        def WhichOneof(self, _):
            return "start"
    await host.handle_control(StartRC())
    assert host.killed is False


@pytest.mark.asyncio
async def test_status_report_shape(tmp_path):
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    host.robots["r1"].runtime._apply_fill("buy", 1, 89000.0, symbol="RIU6")
    rep = host.status_report()
    assert len(rep.robots) == 1
    st = rep.robots[0]
    assert st.robot_id == "r1"
    assert st.position == 1
    assert st.running is True
    assert len(st.recent_fills) == 1
    assert st.recent_fills[0].price == 89000.0
