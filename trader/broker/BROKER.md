# Broker Interface SDK — build brief

Goal: robots trade through ONE strict contract; the concrete broker is chosen from
settings (no hardcode); adapters are pluggable products. Contract is `trader/broker/base.py`
(DONE): `BrokerInterface`, data models, `Capability`, `CORE_CAPABILITIES` (9),
`SECOND_WAVE_CAPABILITIES` (6), `is_trade_ready()`.

## CORE (9) every trade-ready adapter must implement
instruments, order_book, place_order, cancel_order, **replace_order (native atomic move)**,
orders, positions, account, connection.

## Components (sub-agent ownership)
- **P** `trader/broker/` (Python): `registry.py` (factory + trade-ready gate), `finam.py`
  (FinamBroker over the Finam Trade API), `quik.py` (QuikBroker over the agent's HTTP
  order/status routes + status), a demo robot, tests, and SDK doc. Robots import ONLY
  base.py + registry; never a concrete adapter.
- **G** `quik_agent/` (Go+Lua) + `trader/quik|api` (Python glue): native MOVE_ORDERS.
  proto `ReplaceOrder` (DONE) -> agent manager handles it -> bridge "move" cmd -> Lua
  `sendTransaction{ACTION=MOVE_ORDERS, MODE=0, FIRST_ORDER_NUMBER, FIRST_ORDER_NEW_PRICE
  [, FIRST_ORDER_NEW_QUANTITY]}` -> OnOrder/OnTransReply back. The 1b maker loop re-quotes
  via MOVE (atomic), NOT cancel+place. Add a STL route POST /api/v1/quik/orders/replace +
  store handling so QuikBroker.replace_order works end to end.

## registry.py (P)
- `register(name)` decorator; `get_broker(settings) -> BrokerInterface` picks by
  `settings.exchange_interface` (e.g. "finam" | "quik").
- HARD GATE: if the selected adapter is not `is_trade_ready()`, raise — a partial adapter
  can never be handed to a robot for live trading. Read-only use may bypass with a flag.
- Adapter config (endpoints, account, tokens) by setting/env NAME only — never hardcoded.

## QuikBroker (P) — maps to the agent we built
- order_book/orders/positions(*)/account(*)/connection from the agent status + /quik routes.
- place_order/cancel_order/replace_order via /api/v1/quik/orders/{place,cancel,replace}.
- instruments from the params/securities the agent streams.
- (*) positions/account need the agent to report them; if not yet available, QuikBroker must
  NOT claim those CORE caps -> it is honestly not trade-ready until G/agent provide them.
  Flag this clearly rather than faking. MAKER_EXECUTION (second wave) maps to start/stop-execution.

## FinamBroker (P)
- Wrap the existing Finam Trade API client (trader/md, trader/tx, trader/pos). Map
  instruments/order_book/quote/place/cancel/replace/orders/positions/account/connection.
  Only claim REPLACE_ORDER if Finam has a native atomic replace; else omit it (and the
  adapter is not trade-ready on that cap — surface it, do not emulate silently).

## Guard 3 / safety
Read-only/design now (market closed). No live order is sent from this work. Keep the
human-initiated + kill-switch + limits already in the agent. Native MOVE is ONE op (no
zero/two-live-orders window) — it also hardens the maker loop against the runaway class.
