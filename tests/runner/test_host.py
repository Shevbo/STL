import json

import pytest

from robot_runner.host import RobotHost
from trader.lab.strategies.library import make_on_bar


@pytest.mark.asyncio
async def test_make_on_bar_raises_without_symbol_in_params():
    # Independent root-cause proof (strategy layer, no host): make_on_bar reads
    # params["symbol"] first thing, so symbol-less params raise KeyError('symbol')
    # -> str(exc) == "'symbol'", exactly the host.on_bar_failed error seen on
    # agent-fvg-RIU6-v3. This is WHY the host must backfill symbol from spec.
    on_bar = make_on_bar("fvg")
    with pytest.raises(KeyError) as ei:
        await on_bar(object(), {"qty": 1})   # object() is untouched: fails before any call
    assert str(ei.value) == "'symbol'"


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


def _deploy_rc(robot_id="r1", strategy="fvg", paper=True, params=None):
    """Duck-typed RunnerControl stand-in for handle_control."""
    class Spec:
        pass
    spec = Spec()
    spec.robot_id = robot_id
    spec.strategy_id = strategy
    if params is None:
        params = {"symbol": "RIU6", "qty": 1, "min_frac": 12,
                  "tp_atr": 60, "avg_max": 1, "avg_atr_n": 5,
                  "avg_step_atr": 24}
    spec.params_json = json.dumps(params)
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
async def test_deploy_injects_spec_symbol_when_params_json_omits_it(tmp_path):
    # Regression (agent-fvg-RIU6-v3): a deploy/edit route sent params_json WITHOUT
    # "symbol". make_on_bar reads params["symbol"] -> KeyError 'symbol' on EVERY
    # bar (host.on_bar_failed error="'symbol'"), so the robot never traded. The
    # host must backfill params.symbol from the authoritative spec.symbol.
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc(params={"qty": 1, "min_frac": 12,
                                                 "tp_atr": 60, "avg_max": 1,
                                                 "avg_atr_n": 5, "avg_step_atr": 24}))
    r = host.robots["r1"]
    assert r.spec["params"]["symbol"] == "RIU6"   # backfilled from spec.symbol
    # and the strategy now runs clean instead of raising KeyError('symbol')
    t0 = 1_751_500_000_000
    for i in range(12):
        r.bars.on_tick(t0 + i * 60_000, 89_000 + i * 30)
    assert await host.tick_robot(r) is True
    assert r.last_error == ""


@pytest.mark.asyncio
async def test_set_params_reinjects_spec_symbol(tmp_path):
    # A light SetRobotParams (params_json only) that omits symbol must not strip
    # it back out — otherwise the next bar throws KeyError('symbol') again.
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    r = host.robots["r1"]

    class _SetParams:
        class SP:
            robot_id = "r1"
            params_json = json.dumps({"qty": 2, "min_frac": 15})  # no symbol
        set_params = SP()

        def WhichOneof(self, _):  # noqa: N802 (proto API shape)
            return "set_params"

    await host.handle_control(_SetParams())
    assert r.spec["params"]["symbol"] == "RIU6"   # re-injected, not lost
    assert r.spec["params"]["qty"] == 2           # the edit still applied
    t0 = 1_751_500_000_000
    for i in range(12):
        r.bars.on_tick(t0 + i * 60_000, 89_000 + i * 30)
    assert await host.tick_robot(r) is True
    assert r.last_error == ""


@pytest.mark.asyncio
async def test_per_robot_event_log_records_significant_events(tmp_path):
    # «Детальный лог робота»: the runner appends significant events to
    # <data>/logs/<robot_id>.log — deploy (LIFECYCLE), fills (FILL), and a
    # max_position refusal (SKIP). The agent serves this file at /logs/robot/<id>.
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc(paper=True))
    logf = tmp_path / "logs" / "r1.log"
    assert logf.exists()
    assert "[LIFECYCLE]" in logf.read_text(encoding="utf-8")   # deploy logged

    r = host.robots["r1"]
    await r.runtime.place_order("RIU6", "buy", 1, 89_000.0)     # paper -> instant FILL
    text = logf.read_text(encoding="utf-8")
    assert "[FILL]" in text and "позиция 1" in text
    # growing past max_position (=1) is refused and logged, not silently dropped
    await r.runtime.place_order("RIU6", "buy", 5, 89_000.0)
    assert "[SKIP]" in logf.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_event_log_disabled_without_dir_never_raises(tmp_path):
    # No event_log_dir (STL-side paper robots, tests) -> event() is console-only,
    # never touches disk, never raises.
    from robot_runner.runtime import AgentRuntime
    rt = AgentRuntime("r9", FakeBridge(), None, paper=True)   # no event_log_dir
    rt.event("ORDER", "smoke")                                 # must be a no-op on disk
    assert rt._event_log_path is None


@pytest.mark.asyncio
async def test_arming_paper_to_real_resets_stats_keeps_bars(tmp_path):
    # Operator arming: flipping a PAPER robot to REAL re-deploys the same spec.
    # The paper-era P&L/fills must reset to zero (real money starts clean), but the
    # warmed bar history must survive (no silent re-warm).
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc(paper=True))
    r = host.robots["r1"]
    r.runtime.restore(position=0, avg=0.0, realized=1234.5,
                      fills=[{"side": "buy", "qty": 1, "price": 100.0, "status": "paper",
                              "order_id": "o", "client_id": "c", "symbol": "RIU6", "ts_ms": 1}])
    t0 = 1_751_500_000_000
    for i in range(12):
        r.bars.on_tick(t0 + i * 60_000, 89_000 + i * 30)
    bars_before = len(r.bars.bars())
    host.persist()

    await host.handle_control(_deploy_rc(paper=False))   # flip paper -> REAL
    r2 = host.robots["r1"]
    assert r2.runtime.realized_pnl() == 0.0          # P&L reset
    assert r2.runtime.fills_tail() == []             # trade history reset
    assert len(r2.bars.bars()) == bars_before        # bars KEPT (no re-warm)

    # a plain REAL->REAL re-deploy (e.g. params change) must NOT wipe real history
    r2.runtime.restore(position=0, avg=0.0, realized=555.0, fills=[])
    host.persist()
    await host.handle_control(_deploy_rc(paper=False))
    assert host.robots["r1"].runtime.realized_pnl() == 555.0


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
async def test_flatten_market_closes_and_pauses(tmp_path):
    # Operator flatten: cancel working orders, market-close the whole position
    # (opposite side, abs(signed)), and pause until an explicit start.
    bridge = FakeBridge()
    host = RobotHost(bridge, str(tmp_path))
    await host.handle_control(_deploy_rc(paper=False))
    r = host.robots["r1"]
    t0 = 1_751_500_000_000
    for i in range(3):
        r.bars.on_tick(t0 + i * 60_000, 89_000 + i * 30)
    r.runtime.restore(position=5, avg=89_000.0, realized=0.0)   # believe +5 long

    class _Flatten:
        class F:
            robot_id = "r1"
        flatten = F()
        def WhichOneof(self, _):  # noqa: N802 (proto API shape)
            return "flatten"

    bridge.placed.clear()
    await host.handle_control(_Flatten())
    assert r.paused is True
    # exactly one market close order, opposite the long, for the full size
    assert len(bridge.placed) == 1
    assert bridge.placed[0]["side"] == "sell"
    assert bridge.placed[0]["qty"] == 5


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


@pytest.mark.asyncio
async def test_standalone_strategy_us_open_anchors_msk_on_utc_bars(tmp_path):
    """us_open_fvg is a STANDALONE module (not in the library REGISTRY): the host
    must resolve it via import, and — the real-money part — the runner's bars
    are TRUE UTC while the strategy thinks in MSK wall time, so the deploy passes
    bar_offset_min=180. With the offset the 16:30 MSK opening range is anchored
    at 13:30 UTC; without it the strategy would wait for 19:30 MSK (seen before
    it ever shipped: this test is the guard)."""
    from datetime import datetime, timezone

    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc(
        robot_id="usopen", strategy="us_open_fvg",
        params={"symbol": "RIU6", "qty": 1, "bar_offset_min": 180,
                "range_min": 5, "entry_mode": 1}))
    r = host.robots["usopen"]

    def utc(h, m):
        return int(datetime(2026, 7, 14, h, m, tzinfo=timezone.utc).timestamp())

    # trades minute-by-minute across the US open (13:30 UTC == 16:30 MSK)
    for i, (h, m) in enumerate([(13, 28), (13, 29), (13, 30), (13, 31), (13, 32),
                                (13, 33), (13, 34), (13, 35), (13, 36)]):
        r.bars.on_trade(utc(h, m) * 1000, 87000.0 + i * 10, 1)
        await host.tick_robot(r)

    assert r.last_error == ""                      # module resolved and ran clean
    rh = r.runtime.get_state("rh")
    rl = r.runtime.get_state("rl")
    assert rh is not None and rl is not None       # opening range BUILT at 16:30 MSK
    # range = bars of 13:30..13:34 UTC (prices 87020..87060)
    assert rl >= 87000.0 and rh <= 87070.0


@pytest.mark.asyncio
async def test_bars_survive_runner_restart(tmp_path):
    """Restart immunity: closed bars persist in runner_state.json and re-warm a
    fresh host, so a long-lookback robot (order_block needs 116 M1 bars = ~2h)
    is combat-ready immediately instead of blind after every agent restart."""
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    r = host.robots["r1"]
    base = 1_784_000_000_000 - (1_784_000_000_000 % 60_000)
    for i in range(10):                       # 9 closed bars + 1 forming
        r.bars.on_trade(base + i * 60_000, 87000.0 + i, 1)
    await host.tick_robot(r)                  # runs + persists (with bars)
    closed = len(r.bars.bars())
    assert closed == 9

    host2 = RobotHost(FakeBridge(), str(tmp_path))   # "restart"
    await host2.handle_control(_deploy_rc())
    r2 = host2.robots["r1"]
    assert len(r2.bars.bars()) == closed              # re-warmed, not blind
    assert r2.bars.bars()[-1].close == 87008.0
    # the restored newest bar was already executed pre-restart: no re-run
    assert r2.last_bar_run == r2.bars.last_bar_time
    assert await host2.tick_robot(r2) is False
    # live tape continues cleanly after the seed (no duplicate/backward minutes)
    r2.bars.on_trade(base + 10 * 60_000, 87020.0, 1)  # closes bar 9
    r2.bars.on_trade(base + 11 * 60_000, 87030.0, 1)
    assert len(r2.bars.bars()) == closed + 1
    assert await host2.tick_robot(r2) is True         # NEW bar -> strategy runs
    # a stale replayed trade older than the restored tail is ignored
    r2.bars.on_trade(base, 1.0, 1)
    assert r2.bars.bars()[0].close != 1.0
