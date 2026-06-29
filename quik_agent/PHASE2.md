# Phase 2 — orders & maker execution (Slice 1)

HUMAN-INITIATED ONLY. Orders are decided + confirmed by the operator in the STL UI.
The agent and STL each enforce hard limits (defense in depth). No strategy/signals —
only placement and maker-working of a human-decided order. Live account, small size.

## Components (sub-agent ownership)
- **L** `quik_agent/lua/` (new): QUIK Lua script — sendTransaction + OnTransReply/OnOrder/
  OnTrade callbacks; a localhost TCP bridge to the Go agent.
- **A** `quik_agent/internal/` (Go): TCP bridge to Lua, order gRPC messages, hard limits +
  price collar + kill-switch, and the 1b maker-execution loop (sub-second, local book).
- **C** `trader/` (Python): receive/store orders + executions, second-line limits, API +
  UI "Заявки" with a confirm dialog + kill-switch button.
- **E** `docs/`: live-account acceptance plan for orders.

## gRPC contract (STL <-> agent) — already in the proto
`proto/shectory/quik/v1/quik_agent.proto`:
- STL->agent: `PlaceOrder`, `CancelOrder`, `KillSwitch`, `StartExecution`, `StopExecution`.
- agent->STL: `OrderUpdate` (PENDING/ACTIVE/PARTIAL/FILLED/CANCELLED/REJECTED), `TransReply`,
  `ExecutionUpdate` (1b progress). Enums `Side`, `OrderState`.

## Lua <-> agent TCP protocol (L and A MUST agree)
- Agent runs a TCP server on `127.0.0.1:<trade_bridge_port>` (config, default 50063). The
  Lua script (LuaSocket `socket.tcp`) connects as client and reconnects on drop. If QUIK
  lacks LuaSocket, L documents a file-queue fallback in the same JSON schema.
- Newline-delimited JSON, one object per line.
- agent -> Lua:
  - `{"cmd":"place","trans_id":N,"client_id":"..","class":"SPBFUT","sec":"RIU6","op":"B|S","price":"..","qty":K,"type":"L","account":".."}`
  - `{"cmd":"cancel","trans_id":N,"order_num":"..","class":"SPBFUT","sec":"RIU6"}`
- Lua -> agent (from QUIK callbacks):
  - `{"event":"trans_reply","trans_id":N,"result_code":I,"order_num":"..","text":".."}`
  - `{"event":"order","order_num":"..","trans_id":N,"state":"active|filled|cancelled|rejected","balance":B,"qty":Q,"price":"..","text":".."}`  (balance = unfilled remainder)
  - `{"event":"trade","order_num":"..","qty":Q,"price":"..","ts":..}`
- The Lua maps `place` to `sendTransaction{TRANS_ID, ACTION=NEW_ORDER, CLASSCODE, SECCODE,
  OPERATION=B/S, PRICE, QUANTITY, TYPE=L, ACCOUNT, CLIENT_CODE}` and `cancel` to
  `ACTION=KILL_ORDER, ORDER_KEY=order_num`. TRANS_ID is the agent-assigned correlation id.

## Hard limits (agent-enforced; STL enforces the same first)
Config (extend internal/config + STL settings), defaults agreed with the operator:
- `max_contracts_per_order` = 2
- `max_working_contracts` = 2 (total resting)
- `price_collar_frac` = 0.002 (0.2% max adverse deviation from the order/arrival price)
- `instrument_whitelist` = ["RIU6"] (reject anything else)
- `daily_order_cap` = e.g. 50 placements/day
- `quik_trading_enabled` = false (master flag; both agent and STL; orders rejected when off)
A request failing ANY limit is rejected BEFORE reaching Lua/QUIK, with an OrderUpdate
REJECTED + reason. Limits live in code, secrets/account via keymaster — never hardcoded.

## 1b maker-execution loop (in the agent, sub-second)
- Place a limit JOINING our side's best (best bid for BUY, best ask for SELL). NEVER cross
  the opposite touch -> always maker (lower commission), never taker.
- Re-quote (cancel/replace) when our side's touch moves, but only if it moved >= 1 price
  step AND no more often than every 200 ms (anti-flicker).
- Slippage collar: never quote or fill beyond `worst_price`. If the market runs past the
  collar before target is filled: STOP, cancel the remainder, emit ExecutionUpdate
  state=collar_hit. Do NOT chase / do NOT become taker unless `allow_cross=true` (default off).
- Accumulate partial fills toward `target_quantity`; finish at target or collar.
- Reads the LOCAL order book (quikdde provider) + Lua order events; no STL round-trip per tick.

## Staging (safe on a live account)
- **1a**: single manual limit place / cancel / status on 1 contract — prove the Lua pipe.
- **1b**: enable the maker loop on top, only after 1a passes acceptance.
Build the full contract now; gate the sub-second loop behind `StartExecution` (explicit,
confirmed) and `quik_trading_enabled`.

## Guard 3 (human-only / irreversible)
- The agent NEVER places/cancels without an explicit, confirmed STL command. No auto-trading.
- KillSwitch cancels all working orders and blocks new placements until explicitly cleared.
- Real money: every placement is operator-confirmed in the UI (instrument, side, price, qty,
  notional/margin, maker commission estimate).
