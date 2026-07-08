# Agent robot order execution — maker/taker + non-fill stacking

Status: investigated 2026-07-08 on the LIVE robot `agent-fvg-RIU6-v2`. Fixes designed +
one unit-tested. NOT deployed to the live runner (needs operator-supervised cutover; see
Deploy). Author: klod-stl session.

## Symptom (live)

Robot frozen: `position=+1`, `working_orders=8` (all BUY, 4 price levels doubled),
`realized_pnl` stuck at -3690 points / ~-5618 rub, `recon=MISMATCH`, bars advancing. The
robot is not making new state changes — it placed reversal orders that never fill.

## Root cause: orders are LIMIT at bars[-1].close (maker), and rest unfilled live

- Robot orders are always LIMIT (`quik_agent/internal/trade/bridge.go:520` `p.Type = "L"`;
  the Lua supports "M" market too but the agent only sends "L"). Price = the strategy's
  `bars[-1].close` (`robot_runner/runtime.py` place_order, from `library.py` on_bar).
- The strategy (`trader/lab/strategies/library.py` `make_on_bar`) re-derives its intended
  orders every bar from the FILLED position (`stl.get_position` = runtime `_signed`, which
  only advances on fill events). In backtest/paper the order fills the SAME bar, so the
  position updates and nothing carries over.
- LIVE: a limit at last close is usually away from the market by the time it lands, so it
  RESTS unfilled. The filled position never changes, so each subsequent bar RE-EMITS the
  same reversal (close + open, `library.py:74-76`, same side when reversing short->long) =
  stacking. Hence 8 resting BUYs with `max_position=1`, a stuck position, recon MISMATCH
  (robot claims a position/orders QUIK can't corroborate), and losses (missed exits).
- The same-price same-side PAIRS are the legit reversal (close abs(cur) + open base_unit),
  NOT a duplicate bug. Operator was right about that.

## Over-exposure hazard

The 8 resting BUYs each passed the pre-send max_position guard (checked at place time
against the then-current filled position, not against resting orders). If the market drops
through their prices, several fill at once -> position can exceed max_position=1 by many
contracts. The watch alerts on `|position| > max_position`.

## Fix A (SAFE, unit-tested): cancel this robot's working orders before each new-bar on_bar

`robot_runner/host.py` `tick_robot`, right after the new-closed-bar guard: iterate
`r.runtime.working_orders()` and `await r.runtime.cancel_order(w["order_id"])` before
`await r.on_bar(...)`. Restores backtest parity (backtest has no carryover orders) and
bounds the pile to the latest bar's intent. No-op in paper (no resting orders). Tests:
`test_real_robot_cancels_resting_orders_before_next_bar`, `test_paper_tick_never_cancels`
(FakeBridge gains `cancel_order`). Caveat: brief cancel/replace window (old + new order
both live until the cancel confirms) — fine for 1-min bars. Does NOT make orders fill, so
the position can still diverge — pair with Fix B.

## Fix B (DEEPER, needs operator + agent rebuild): marketable/taker execution

Make entry/exit orders fill immediately like the backtest. Options:
1. Marketable LIMIT: runner prices BUY at `best_ask + collar`, SELL at `best_bid - collar`
   (runner has bid/ask from the tick stream; thread them into place_order). Stays inside
   the existing price-collar limit check.
2. Market order (`type="M"`): Lua already supports it; the Go manager's price-collar check
   must special-case market (no price) — an agent-side (Go) change + rebuild + publish.
Cost: pay the spread + taker commission — but that MATCHES the backtest's fill-at-close
assumption, so it is the correct parity fix (ties into slippage + commission analysis).

## Deploy (operator-supervised ONLY — real money)

1. Kill-switch the robot (block new + cancel working) so the current 8 orphan BUYs are
   cancelled cleanly BEFORE any runner restart (a restart loses the runner's view of them;
   they would strand at QUIK).
2. Build `robot-runner.exe` on Windows (`bash deploy/build_runner.sh`), stage in
   `~/quik_build/quik_agent/dist/`, `bash ~/quik_build/publish_quik_agent.sh <agent_id>`.
   Agent self-updates + restarts the runner (position/PnL survive via runner_state.json).
3. Re-arm; watch the first reversal fill cleanly (position flips, no stack).
4. If deploying Fix B: reload the marketable-order runner AND confirm the agent build
   handles the order type; walk 1 contract with the operator watching + kill-switch ready.
