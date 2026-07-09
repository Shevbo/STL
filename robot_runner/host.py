"""RobotHost — schedules deployed robots, relays control, reports status.

One on_bar per CLOSED 1-min bar per robot (backtest parity: the strategy sees
the same bar cadence the backtester replays). Local persistence is the source
of truth: specs live in the agent's robots.json (replayed as Deploy on every
control-stream connect); runtime state (strategy state dict, position, avg,
realized P&L) lives in <data_dir>/runner_state.json, written atomically.
"""

import asyncio
import json
import os
import time
from datetime import datetime

import structlog

from trader.lab.scheduler import _MSK, _parse_window, _within_window
from trader.lab.strategies.library import make_on_bar
from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as pb

from robot_runner.bars import BarBuilder, pick_price
from robot_runner.explain import explain
from robot_runner.runtime import AgentRuntime

log = structlog.get_logger()

STATUS_INTERVAL_S = 15.0


class HostedRobot:
    def __init__(self, spec: dict, runtime: AgentRuntime, bars: BarBuilder) -> None:
        self.spec = spec
        self.runtime = runtime
        self.bars = bars
        self.paused = False
        self.last_bar_run = 0        # newest closed-bar time already executed
        self.on_bar = make_on_bar(spec["strategy_id"])
        self.window = _parse_window(spec.get("schedule"))
        self.last_error = ""


class RobotHost:
    def __init__(self, bridge, data_dir: str) -> None:
        self._bridge = bridge
        self._data_dir = data_dir
        self._state_path = os.path.join(data_dir, "runner_state.json")
        self.robots: dict[str, HostedRobot] = {}
        self.killed = False
        # freshest QUIK quote per symbol: code -> (bid, ask, ts_ms). Runtimes
        # price REAL orders marketable off it (see AgentRuntime.place_order).
        self.quotes: dict[str, tuple[float, float, int]] = {}
        self._saved = self._load()

    # ---- persistence ----

    def _load(self) -> dict:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def persist(self) -> None:
        out = {}
        for rid, r in self.robots.items():
            out[rid] = {"state": r.runtime.state,
                        "position": r.runtime.signed_position(),
                        "avg": r.runtime.avg_price(),
                        "realized": r.runtime.realized_pnl(),
                        # order/fill history survives runner restarts (the operator's
                        # audit trail; P&L without its trades looked like a bug)
                        "fills": r.runtime.fills_tail()}
        # keep saved state for robots not currently deployed (undeploy != wipe)
        for rid, saved in self._saved.items():
            out.setdefault(rid, saved)
        tmp = self._state_path + ".tmp"
        os.makedirs(self._data_dir, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f)
        os.replace(tmp, self._state_path)
        self._saved = out

    # ---- control ----

    async def handle_control(self, rc) -> None:
        kind = rc.WhichOneof("payload") if hasattr(rc, "WhichOneof") else None
        if kind == "deploy":
            spec_pb = rc.deploy.spec
            spec = {
                "robot_id": spec_pb.robot_id,
                "strategy_id": spec_pb.strategy_id,
                "params": json.loads(spec_pb.params_json or "{}"),
                "symbol": spec_pb.symbol,
                "schedule": spec_pb.schedule,
                "max_position": int(spec_pb.max_position_contracts or 1),
                "paper": bool(spec_pb.paper),
            }
            saved = self._saved.get(spec["robot_id"], {})
            # keep accumulated bars across a re-deploy (params change, STL reconnect)
            bars = (self.robots[spec["robot_id"]].bars
                    if spec["robot_id"] in self.robots else BarBuilder())
            sym = spec["symbol"]
            rt = AgentRuntime(spec["robot_id"], self._bridge, bars,
                              max_position=spec["max_position"],
                              paper=spec["paper"], state=saved.get("state"),
                              quote_fn=lambda s=sym: self.quotes.get(s))
            rt.restore(position=saved.get("position", 0),
                       avg=saved.get("avg", 0.0),
                       realized=saved.get("realized", 0.0),
                       fills=saved.get("fills"))
            self.robots[spec["robot_id"]] = HostedRobot(spec, rt, bars)
            log.info("host.deployed", robot_id=spec["robot_id"],
                     strategy=spec["strategy_id"], paper=spec["paper"],
                     max_position=spec["max_position"])
            self.persist()
        elif kind == "undeploy":
            self.robots.pop(rc.undeploy.robot_id, None)
            self.persist()
        elif kind == "set_params":
            r = self.robots.get(rc.set_params.robot_id)
            if r is not None:
                r.spec["params"] = json.loads(rc.set_params.params_json or "{}")
                log.info("host.params_updated", robot_id=rc.set_params.robot_id)
        elif kind == "pause":
            r = self.robots.get(rc.pause.robot_id)
            if r is not None:
                r.paused = True
        elif kind == "start":
            r = self.robots.get(rc.start.robot_id)
            if r is not None:
                r.paused = False
            self.killed = False   # an explicit start clears a kill
        elif kind == "kill":
            self.killed = True    # block all new orders; agent cancels working ones
            log.warning("host.kill_switch", reason=getattr(rc.kill, "reason", ""))
        elif kind == "flatten":
            # Operator: cancel working orders, MARKET-close the whole open position
            # (marketable via the real order path, tagged rr: so the fill zeroes the
            # runner's own book), then PAUSE until an explicit Start. Unlike kill,
            # this actually exits the position.
            r = self.robots.get(rc.flatten.robot_id)
            if r is None:
                log.warning("host.flatten_unknown_robot", robot_id=rc.flatten.robot_id)
                return
            for w in r.runtime.working_orders():
                try:
                    await r.runtime.cancel_order(w["order_id"])
                except Exception:  # noqa: BLE001
                    r.runtime.expire_order(w["order_id"])
            signed = r.runtime.signed_position()
            if signed != 0:
                bars = r.bars.bars(1)
                px = bars[-1].close if bars else 0.0
                await r.runtime.place_order(r.spec["symbol"],
                                            "sell" if signed > 0 else "buy",
                                            abs(signed), px)
            r.paused = True
            log.warning("host.flatten", robot_id=rc.flatten.robot_id, closed=signed)
            self.persist()
        elif kind == "fix_state":
            fx = rc.fix_state
            r = self.robots.get(fx.robot_id)
            if r is None:
                log.warning("host.fix_state_unknown_robot", robot_id=fx.robot_id)
                return
            # Recon align: force the believed book to the QUIK fact. Journal +
            # persist immediately so the fix survives a runner restart even if
            # no bar ever closes again today.
            r.runtime.apply_fix(position=int(fx.set_position),
                                avg=float(fx.set_avg_price),
                                clear_working=bool(fx.clear_working),
                                note=fx.note, symbol=r.spec.get("symbol", ""))
            log.warning("host.fix_state", robot_id=fx.robot_id,
                        position=int(fx.set_position),
                        avg=float(fx.set_avg_price),
                        clear_working=bool(fx.clear_working), note=fx.note)
            self.persist()

    # ---- scheduling ----

    async def tick_robot(self, r: HostedRobot) -> bool:
        """Run on_bar once if there is a NEW closed bar, inside the window, not
        paused/killed. Returns True when the strategy executed."""
        if self.killed or r.paused:
            return False
        if not _within_window(datetime.now(_MSK), *r.window):
            return False
        last = r.bars.last_bar_time
        if last == 0 or last == r.last_bar_run:
            return False
        # Backtest parity + real-money safety: in backtest/paper every bar's order
        # fills the SAME bar, so nothing carries over. A REAL limit order can REST
        # unfilled; the strategy re-derives its intent from the FILLED position each
        # bar and would re-emit (stack) the same orders every bar -> unbounded
        # duplicate exposure (seen live: 8 resting BUYs at max_position=1). Cancel
        # this robot's working orders before re-running the strategy so each bar
        # starts order-flat, exactly like the backtest. No-op in paper.
        for w in r.runtime.working_orders():
            try:
                await r.runtime.cancel_order(w["order_id"])
            except Exception as exc:  # noqa: BLE001 — a failed cancel must not skip the bar
                # The agent can't cancel an order it doesn't know (placed before
                # an agent restart / day-expired at QUIK). Terminate it LOCALLY
                # or the book turns phantom (8 non-existent orders shown live).
                r.runtime.expire_order(w["order_id"])
                log.warning("host.precancel_expired_local", robot_id=r.spec["robot_id"],
                            client_id=w["order_id"], error=str(exc))
        try:
            await r.on_bar(r.runtime, r.spec["params"])
            r.last_error = ""
        except Exception as exc:  # noqa: BLE001 — one robot's error never kills the host
            r.last_error = str(exc)
            log.error("host.on_bar_failed", robot_id=r.spec["robot_id"], error=str(exc))
        r.last_bar_run = last
        self.persist()
        return True

    def status_report(self) -> pb.RobotStatusReport:
        robots = []
        for rid, r in self.robots.items():
            fills = [pb.RobotFill(
                order_id=f["order_id"],
                symbol=f["symbol"] or r.spec["symbol"],
                side=pb.SIDE_BUY if f["side"] == "buy" else pb.SIDE_SELL,
                qty=f["qty"], price=f["price"], status=f["status"],
                # FULL persisted tail (200), not the last 20: the operator's
                # showcase must not forget yesterday's trades.
                ts_unix_ms=f["ts_ms"]) for f in r.runtime.fills_tail()]
            working = [pb.RobotWorkingOrder(
                client_id=w["client_id"], order_id=w["order_id"],
                side=pb.SIDE_BUY if w["side"] == "buy" else pb.SIDE_SELL,
                price=w["price"], qty=w["qty"], state=w["state"])
                for w in r.runtime.working_orders()]
            try:
                sig = json.dumps(explain(r.spec["strategy_id"], r.bars.bars(),
                                         r.spec["params"],
                                         r.runtime.signed_position(),
                                         avg=r.runtime.avg_price()),
                                 ensure_ascii=False)
            except Exception as exc:  # noqa: BLE001 — showcase must never break status
                sig = json.dumps({"error": str(exc)}, ensure_ascii=False)
            robots.append(pb.RobotStatus(
                robot_id=rid, running=not (self.killed or r.paused), paused=r.paused,
                position=r.runtime.signed_position(), avg_price=r.runtime.avg_price(),
                realized_pnl=r.runtime.realized_pnl(),
                last_bar_unix=r.bars.last_bar_time,
                heartbeat_unix_ms=int(time.time() * 1000),
                recent_fills=fills, note=r.last_error,
                signal_json=sig, working_orders=working,
                bars_count=len(r.bars.bars()),
                symbol=r.spec["symbol"], strategy_id=r.spec["strategy_id"],
                paper=r.spec["paper"], schedule=r.spec["schedule"],
                params_json=json.dumps(r.spec["params"], ensure_ascii=False),
                max_position=r.spec["max_position"]))
        return pb.RobotStatusReport(robots=robots,
                                    sent_at_unix_ms=int(time.time() * 1000))

    # ---- main loop ----

    async def run(self) -> None:
        async def consume_control():
            async for rc in self._bridge.control("robot-runner/1"):
                await self.handle_control(rc)

        async def consume_tape():
            # Exact bars: every exchange trade (price/qty) from the anonymized
            # tape -> OHLCV identical to what the backtest replays.
            async for b in self._bridge.tape([]):
                for r in self.robots.values():
                    if r.spec["symbol"] == b.code:
                        for t in b.trades:
                            r.bars.on_trade(t.ts_unix_ms, t.price, int(t.qty))

        async def consume_ticks():
            async for t in self._bridge.ticks([]):
                # freshest bid/ask for marketable order pricing (0s tolerated;
                # the runtime validates >0 and freshness before using them)
                self.quotes[t.code] = (float(t.bid or 0), float(t.ask or 0),
                                       int(t.received_at_unix_ms or 0))
                price = pick_price(t.last, t.bid, t.ask)
                if price <= 0:
                    continue
                for r in self.robots.values():
                    if r.spec["symbol"] == t.code:
                        r.bars.on_tick(t.received_at_unix_ms, price)

        async def consume_events():
            async for u in self._bridge.order_events("rr:"):
                for r in self.robots.values():
                    r.runtime.on_order_event(u)
                self.persist()

        async def schedule():
            while True:
                for r in list(self.robots.values()):
                    await self.tick_robot(r)
                await asyncio.sleep(1.0)   # cheap check; on_bar gated by new-closed-bar

        async def report():
            while True:
                await self._bridge.report_status(self.status_report())
                await asyncio.sleep(STATUS_INTERVAL_S)

        await asyncio.gather(consume_control(), consume_ticks(), consume_tape(),
                             consume_events(), schedule(), report())
