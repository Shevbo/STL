"""RobotHost — schedules deployed robots, relays control, reports status.

One on_bar per CLOSED 1-min bar per robot (backtest parity: the strategy sees
the same bar cadence the backtester replays). Local persistence is the source
of truth: specs live in the agent's robots.json (replayed as Deploy on every
control-stream connect); runtime state (strategy state dict, position, avg,
realized P&L) lives in <data_dir>/runner_state.json, written atomically.
"""

import asyncio
import importlib
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
# Сколько закрытых минуток уезжает в отчёт на мини-график панели. 30 = полчаса,
# ровно столько влезает в ширину компаньона по 10 px на бар.
BARS_TAIL_N = 30


def resolve_on_bar(strategy_id: str):
    """Strategy resolution, 1:1 with STL's two families: the parametric REGISTRY
    (library.make_on_bar) first, else a standalone module
    trader.lab.strategies.<id> exporting its own on_bar (donchian_breakout,
    us_open_fvg, ...). Standalone modules are bundled into the exe by
    build.spec's collect_submodules('trader.lab.strategies')."""
    try:
        return make_on_bar(strategy_id)
    except KeyError:
        mod = importlib.import_module(f"trader.lab.strategies.{strategy_id}")
        return mod.on_bar


class HostedRobot:
    def __init__(self, spec: dict, runtime: AgentRuntime, bars: BarBuilder) -> None:
        self.spec = spec
        self.runtime = runtime
        self.bars = bars
        self.paused = False
        self.last_bar_run = 0        # newest closed-bar time already executed
        self.on_bar = resolve_on_bar(spec["strategy_id"])
        self.window = _parse_window(spec.get("schedule"))
        self.last_error = ""
        self.last_want = "unset"     # sentinel: first computed signal always logs


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
                        "realized": r.runtime.realized_gross(),   # GROSS points…
                        "commission": r.runtime.commission_points(),  # …+ fees kept apart
                        # order/fill history survives runner restarts (the operator's
                        # audit trail; P&L without its trades looked like a bug)
                        "fills": r.runtime.fills_tail(),
                        # closed-bars tail: restart immunity for long-lookback
                        # strategies (order_block re-warmed ~2h after every
                        # agent/runner restart; us_open could miss its one daily
                        # setup entirely)
                        "bars": r.bars.to_rows()}
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
            # make_on_bar reads params["symbol"]; spec.symbol is the authoritative
            # symbol (it drives tape/tick/order routing everywhere below). Some
            # deploy/edit routes omit symbol from params_json -> KeyError 'symbol'
            # on every bar (agent-fvg-RIU6-v3 was wedged on exactly this). Keep the
            # params symbol in lockstep with the spec so any route deploys clean.
            if spec["symbol"]:
                spec["params"]["symbol"] = spec["symbol"]
            prev = self.robots.get(spec["robot_id"])
            # ARMING: a LIVE paper->real flip re-deploys this spec. Reset the P&L +
            # fills statistics so the REAL account starts from ZERO — the paper era
            # is not real money (operator request; supersedes the old "never reset at
            # arming"). Detected only on the in-memory paper->real transition, so a
            # plain runner restart / params re-deploy never resets, and a long-running
            # REAL robot's history is safe. The robot is FLAT at arming (the agent's
            # ModeSet gate refuses a non-flat flip); bars are KEPT so it does not
            # re-warm (silent for an hour).
            arming = prev is not None and prev.spec.get("paper") and not spec["paper"]
            saved = self._saved.get(spec["robot_id"], {})
            # keep accumulated bars across a re-deploy (params change, STL reconnect, arming)
            bars = prev.bars if prev is not None else BarBuilder()
            if prev is None:
                # fresh process: re-warm from the persisted tail so a restart never
                # blinds a long-lookback robot (seed() is a no-op once live data flows)
                bars.seed(saved.get("bars") or [])
            sym = spec["symbol"]
            rt = AgentRuntime(spec["robot_id"], self._bridge, bars,
                              max_position=spec["max_position"],
                              paper=spec["paper"], state=saved.get("state"),
                              quote_fn=lambda s=sym: self.quotes.get(s),
                              event_log_dir=self._data_dir)
            # Commission: on the FIRST restore after the fee upgrade the persisted
            # state has no "commission" key — retro-charge the saved fills tail once
            # so the shown P&L doesn't jump from gross to net (best-effort; ≤200 fills,
            # exact for robots newer than that). Later restores read the persisted value.
            saved_comm = saved.get("commission")
            if saved_comm is None and not arming:
                try:
                    from trader.lab.commission import taker_points
                    saved_comm = sum(
                        taker_points(f.get("symbol") or "", f.get("price") or 0, f.get("qty") or 0)
                        for f in (saved.get("fills") or [])
                        if f.get("side") in ("buy", "sell") and f.get("status") in ("filled", "paper"))
                except Exception:
                    saved_comm = 0.0
            # ARMING starts the REAL era FLAT: a paper position is fictional (no real
            # order backs it), so it must NOT carry into real. Resetting position+avg
            # here is the backstop for the flat-gate racing on a stale status — without
            # it a paper +N "teleports" into real as a phantom the runner would try to
            # close with a REAL order (found live 2026-07-20). Paper P&L/fills already reset.
            rt.restore(position=0 if arming else saved.get("position", 0),
                       avg=0.0 if arming else saved.get("avg", 0.0),
                       realized=0.0 if arming else saved.get("realized", 0.0),
                       commission=0.0 if arming else (saved_comm or 0.0),
                       fills=[] if arming else saved.get("fills"))
            hr = HostedRobot(spec, rt, bars)
            # A re-deploy (params edit, paper<->real mode flip, STL reconnect) must
            # PRESERVE the paused state: a fresh HostedRobot defaults to paused=False,
            # so without this a paused robot silently resumed on any re-deploy —
            # a REAL robot would quietly start trading real money again on a params
            # edit, and (seen 2026-07-17) a paper flip left the store paused=True but
            # the runner running, so the operator could not clear the pause. prev is
            # None only on a fresh process, where the agent replays the persisted
            # Pause right after this Deploy anyway.
            if prev is not None:
                hr.paused = prev.paused
            # A restored/kept tail's newest bar was already executed by the previous
            # incarnation (or predates the re-deploy): act only on genuinely NEW bars,
            # never re-run a historical one against live orders.
            hr.last_bar_run = bars.last_bar_time
            self.robots[spec["robot_id"]] = hr
            log.info("host.deployed", robot_id=spec["robot_id"],
                     strategy=spec["strategy_id"], paper=spec["paper"],
                     max_position=spec["max_position"])
            rt.event("LIFECYCLE", f"деплой: стратегия={spec['strategy_id']} "
                     f"режим={'paper' if spec['paper'] else 'РЕАЛ'} "
                     f"max_pos={spec['max_position']} окно={spec['schedule']}")
            if arming:
                rt.event("LIFECYCLE", "АРМИНГ paper->РЕАЛ: статистика обнулена "
                         "(realized P&L и история сделок с нуля)", level="warning")
            self.persist()
        elif kind == "undeploy":
            r = self.robots.get(rc.undeploy.robot_id)
            if r is not None:
                r.runtime.event("LIFECYCLE", "снят с деплоя (undeploy)")
            self.robots.pop(rc.undeploy.robot_id, None)
            self.persist()
        elif kind == "set_params":
            r = self.robots.get(rc.set_params.robot_id)
            if r is not None:
                params = json.loads(rc.set_params.params_json or "{}")
                if r.spec.get("symbol"):     # see deploy: params must carry symbol
                    params["symbol"] = r.spec["symbol"]
                r.spec["params"] = params
                r.runtime.event("LIFECYCLE", "параметры обновлены: "
                                + json.dumps(params, ensure_ascii=False))
                log.info("host.params_updated", robot_id=rc.set_params.robot_id)
        elif kind == "pause":
            r = self.robots.get(rc.pause.robot_id)
            if r is not None:
                r.paused = True
                r.runtime.event("LIFECYCLE", "ПАУЗА (оператор)")
        elif kind == "start":
            r = self.robots.get(rc.start.robot_id)
            if r is not None:
                r.paused = False
                r.runtime.event("LIFECYCLE", "СТАРТ (снята пауза)")
            self.killed = False   # an explicit start clears a kill
        elif kind == "kill":
            self.killed = True    # block all new orders; agent cancels working ones
            reason = getattr(rc.kill, "reason", "")
            for r in self.robots.values():
                r.runtime.event("LIFECYCLE", f"KILL-SWITCH: {reason}", level="warning")
            log.warning("host.kill_switch", reason=reason)
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
            r.runtime.event("LIFECYCLE", f"FLATTEN: закрыта позиция {signed}, ПАУЗА",
                            level="warning")
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
            # no bar ever closes again today. set_pnl: explicit P&L correction
            # (points, gross+commission apart) — the counters can be corrupted
            # too (journal auto-heal replayed old fills, 2026-08-06).
            set_pnl = bool(getattr(fx, "set_pnl", False))
            r.runtime.apply_fix(position=int(fx.set_position),
                                avg=float(fx.set_avg_price),
                                clear_working=bool(fx.clear_working),
                                note=fx.note, symbol=r.spec.get("symbol", ""),
                                realized=float(fx.set_realized_gross_pts) if set_pnl else None,
                                commission=float(fx.set_commission_pts) if set_pnl else None)
            log.warning("host.fix_state", robot_id=fx.robot_id,
                        position=int(fx.set_position),
                        avg=float(fx.set_avg_price),
                        clear_working=bool(fx.clear_working), note=fx.note,
                        set_pnl=set_pnl)
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
                log.warning("host.precancel_failed", robot_id=r.spec["robot_id"],
                            client_id=w["order_id"], error=str(exc))
            finally:
                # Expire the order LOCALLY every bar regardless of the cancel outcome.
                # A bar-close strategy must start order-flat; and QUIK may REJECT the
                # cancel ("Вы не можете снять данную заявку" — the order already
                # filled/expired), which otherwise leaves it "active" forever and
                # re-sends the cancel every bar — a cancel-reject storm every minute
                # (seen live 2026-07-09 18:17-19:30 after a Lua crash). A late fill
                # still applies via on_order_event by client_id, so this is safe.
                r.runtime.expire_order(w["order_id"])
        try:
            # Режим «только на выход» живёт в params робота (инфраструктурный флаг,
            # как bar_offset_min): приходит штатным SetRobotParams, переживает
            # рестарт вместе со спекой, не требует правки протокола.
            r.runtime.exit_only = bool((r.spec.get("params") or {}).get("exit_only"))
            await r.on_bar(r.runtime, r.spec["params"])
            r.last_error = ""
            self._log_signal_change(r)
        except Exception as exc:  # noqa: BLE001 — one robot's error never kills the host
            r.last_error = str(exc)
            log.error("host.on_bar_failed", robot_id=r.spec["robot_id"], error=str(exc))
            r.runtime.event("ERROR", str(exc), console=False, level="error")
        r.last_bar_run = last
        self.persist()
        return True

    def _log_signal_change(self, r: HostedRobot) -> None:
        """Emit a SIGNAL event to the robot's detailed log only when the strategy's
        desired position (want) CHANGES — entry/exit/reversal — not every bar."""
        try:
            sig = explain(r.spec["strategy_id"], r.bars.bars(), r.spec["params"],
                          r.runtime.signed_position(), avg=r.runtime.avg_price(),
                          state=r.runtime.state_snapshot())
        except Exception:  # noqa: BLE001 — introspection must never break the bar
            return
        want = sig.get("want")
        if want == r.last_want:
            return
        r.last_want = want
        reason = sig.get("waiting_for") or ""
        r.runtime.event("SIGNAL", f"want={want} · {reason}", console=False)

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
                                         avg=r.runtime.avg_price(),
                                         state=r.runtime.state_snapshot()),
                                 ensure_ascii=False)
            except Exception as exc:  # noqa: BLE001 — showcase must never break status
                sig = json.dumps({"error": str(exc)}, ensure_ascii=False)
            # Статистика фильтров входа (разножка/остывание) едет наружу ВНУТРИ
            # signal_json — свободного поля отчёта, чтобы не менять proto/агент/STL.
            # saved_pts в ПУНКТАХ: рубли считает карточка через ₽/пункт инструмента.
            try:
                gs = int(r.runtime.get_state("gap_skips", 0) or 0)
                cs = int(r.runtime.get_state("cooldown_skips", 0) or 0)
                ds = int(r.runtime.get_state("dv_skips", 0) or 0)
                if gs or cs or ds:
                    _d = json.loads(sig)
                    if isinstance(_d, dict):
                        _d["filter_stats"] = {
                            "gap_skips": gs, "cooldown_skips": cs, "dv_skips": ds,
                            "saved_pts": round(float(
                                r.runtime.get_state("filter_saved_pts", 0) or 0), 2),
                            "pending": len(r.runtime.get_state("skip_phantoms", None) or []),
                            # с какого момента копится (методика менялась 29.07 —
                            # копилка обнуляется, иначе смешались бы две модели)
                            "since": int(r.runtime.get_state("filter_since", 0) or 0),
                            "dropped": int(r.runtime.get_state("skip_dropped", 0) or 0),
                        }
                        sig = json.dumps(_d, ensure_ascii=False)
            except Exception:  # noqa: BLE001 — статистика никогда не ломает отчёт
                pass
            # Хвост закрытых баров для мини-графика панели: полчаса M1. Это ТЕ ЖЕ
            # бары, по которым робот принимал решение (лента всех сделок), поэтому
            # его заявки и сделки ложатся на свою свечу. Рисовать вместо них бары
            # ISS нельзя: там московское время штамповано как UTC, и филлы уехали
            # бы на три часа. Отчёт уходит раз в 15 с, 30 баров это ~1 КБ.
            bars_tail = [pb.RobotBar(t_unix=b.time, o=b.open, h=b.high,
                                     l=b.low, c=b.close)
                         for b in r.bars.bars(BARS_TAIL_N)]
            robots.append(pb.RobotStatus(
                robot_id=rid, running=not (self.killed or r.paused), paused=r.paused,
                bars_tail=bars_tail,
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
            # Одна битая команда (кривой params_json в persisted-спеке, неизвестная
            # strategy_id после даунгрейда) НЕ должна убивать поток управления:
            # агент реплеит спеки на каждом коннекте, и невыловленная ошибка здесь
            # превращается в вечный краш-луп ВСЕГО раннера — падают и здоровые
            # реальные роботы. Битая команда логируется и пропускается.
            async for rc in self._bridge.control("robot-runner/1"):
                try:
                    await self.handle_control(rc)
                except Exception as exc:  # noqa: BLE001
                    log.error("host.control_failed", error=str(exc),
                              kind=rc.WhichOneof("payload") if hasattr(rc, "WhichOneof") else "?")

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
                # A fill must ALWAYS land in the book. Any exception here used to
                # propagate through gather() and kill the whole runner BEFORE
                # persist — the fill was lost forever and the strategy re-emitted
                # its intent every restart (real-money order pile-up, 2026-07-13).
                try:
                    for r in self.robots.values():
                        r.runtime.on_order_event(u)
                    self.persist()
                except Exception as exc:  # noqa: BLE001
                    log.error("host.order_event_failed", error=str(exc))

        async def schedule():
            # tick_robot ловит ошибки СТРАТЕГИИ, но не свои собственные: persist()
            # на Windows падает PermissionError, когда антивирус держит файл на
            # os.replace — и незащищённая петля умирала, унося ТОРГОВЛЮ всех
            # роботов при живом остальном процессе. Петля обязана пережить всё.
            while True:
                for r in list(self.robots.values()):
                    try:
                        await self.tick_robot(r)
                    except Exception as exc:  # noqa: BLE001
                        log.error("host.tick_failed",
                                  robot_id=r.spec.get("robot_id", "?"), error=str(exc))
                await asyncio.sleep(1.0)   # cheap check; on_bar gated by new-closed-bar

        async def report():
            # report_status глотает сетевые ошибки, но сборка отчёта — нет: одно
            # кривое поле в fills убивало бы репортер навсегда, STL-зеркало
            # застывало, а робот продолжал торговать «невидимым».
            while True:
                try:
                    await self._bridge.report_status(self.status_report())
                except Exception as exc:  # noqa: BLE001
                    log.error("host.report_failed", error=str(exc))
                await asyncio.sleep(STATUS_INTERVAL_S)

        await asyncio.gather(consume_control(), consume_ticks(), consume_tape(),
                             consume_events(), schedule(), report())
