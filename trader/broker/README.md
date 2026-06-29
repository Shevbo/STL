# STL Broker SDK

Robots trade through **one strict contract**. They never talk to a concrete broker
(Finam Trade API, QUIK, ...). They depend only on `trader.broker.base` and pick the
adapter at runtime from settings via `trader.broker.registry`. This keeps STL
broker-, exchange- and interface-agnostic, and lets each adapter ship as an
independent **pluggable product** that drops into any STL installation.

```
robot ──> BrokerInterface (base.py) ──> registry.get_broker(settings) ──> FinamBroker | QuikBroker | <yours>
```

A robot imports **only** `base` + `registry`. Switching brokers is a config
change (`exchange_interface`), not a code change.

## The contract (`base.py`)

`BrokerInterface` is the full surface. An adapter overrides only the methods it
supports and lists them in `capabilities()`. Every unsupported method raises
`UnsupportedCapability` (never a silent no-op), so a robot can rely on the whole
surface and call `supports(cap)` before optional calls.

Broker-neutral data models: `Instrument`, `OrderBook` / `BookLevel`, `Tick`,
`OrderRequest` / `OrderRef` / `Order`, `Position` / `PositionDiff`, `Account`,
`ConnState`, `News`, `TraderMessage`. All `Side` / `OrderType` / `OrderState` /
`LinkState` are enums.

## Capability tiers

`Capability` is the enum of functions. Two tiers:

**CORE (9)** — the minimum to TRADE SAFELY. Missing ANY of these = not trade-ready.

| cap | why it is CORE |
|-----|----------------|
| `INSTRUMENTS` | can't size/price without instrument params |
| `ORDER_BOOK` | can't decide/route without the book |
| `PLACE_ORDER` | can't trade without placing |
| `CANCEL_ORDER` | MUST be able to pull an order (risk) |
| `REPLACE_ORDER` | native **atomic** move — one op, no zero/two-live-orders window |
| `ORDERS` | must see order status |
| `POSITIONS` | over-position guard (real exchange position) |
| `ACCOUNT` | over-leverage guard (margin / free funds) |
| `CONNECTION` | never trade on a dead link |

**SECOND_WAVE (6)** — enhances trading but is emulatable / non-blocking:
`ORDER_BOOK_STREAM`, `QUOTE`, `MAKER_EXECUTION`, `ORDER_STREAM`, `NEWS`,
`MESSAGES`.

`replace_order` MUST be a **native atomic** broker transaction (QUIK
`ACTION=MOVE_ORDERS`, Finam native replace) — never an internal cancel-then-place.
An adapter without a native move **must not** claim `REPLACE_ORDER`.

## The trade-ready gate (`registry.py`)

```python
from trader.broker.registry import get_broker

broker = get_broker(settings)                       # gate ON: refuses a partial adapter
broker = get_broker(settings, require_trade_ready=False)  # read-only / design use
```

`get_broker` picks the adapter by `settings.exchange_interface` and, by default,
raises `BrokerNotTradeReady` naming the missing CORE caps if the adapter is not
`is_trade_ready()`. A partial adapter can never be handed to a robot for live
trading. Unknown names raise `BrokerNotRegistered`. Adapter config (endpoints,
account, tokens) comes from `settings` / keymaster **by name** — never hardcoded.

Adapter dependencies are injected through `get_broker(settings, **inject)` (e.g.
the QUIK `quik_store` / `quik_order_store` / `quik_server` from `app.state`).

## Shipped adapters

### FinamBroker (`finam.py`) — `exchange_interface = "finam"`
Wraps the existing Finam clients (`auth`, `registry`, `md` gRPC, `tx`, `pos`).
Claims `INSTRUMENTS, ORDER_BOOK, QUOTE, PLACE_ORDER, POSITIONS, ACCOUNT,
CONNECTION`. The wrapped `TxClient` has **no** cancel / native replace / read-orders,
so it does **not** claim `CANCEL_ORDER`, `REPLACE_ORDER`, `ORDERS` →
**not trade-ready** until the tx client grows them.

### QuikBroker (`quik.py`) — `exchange_interface = "quik"`
In-process adapter over the QUIK agent link's shared state. Claims `INSTRUMENTS,
ORDER_BOOK, QUOTE, PLACE_ORDER, CANCEL_ORDER, ORDERS, CONNECTION, MAKER_EXECUTION`.
The agent does **not** report positions/account, and native `MOVE_ORDERS` is not
wired yet (no pb field / route), so it does **not** claim `POSITIONS, ACCOUNT,
REPLACE_ORDER` → **not trade-ready** until those land.

## How to write your own adapter

1. Subclass `BrokerInterface`, set `name`.
2. Implement the CORE methods you can (and any second-wave ones).
3. Declare exactly what you implement in `capabilities()` — **honestly**. Do not
   claim a cap you emulate unsafely (especially `REPLACE_ORDER`).
4. Register a factory:

```python
from trader.broker.base import BrokerInterface, Capability
from trader.broker.registry import register

class MyBroker(BrokerInterface):
    name = "mybroker"

    def __init__(self, settings, **inject):
        self._settings = settings   # config by NAME only

    def capabilities(self):
        return {Capability.INSTRUMENTS, Capability.ORDER_BOOK, ...}

    async def order_book(self, symbol, depth=10):
        ...  # return base.OrderBook

@register("mybroker")
def _build(settings, **inject):
    return MyBroker(settings, **inject)
```

5. Select it with `exchange_interface = "mybroker"`. If you implement all 9 CORE
   caps, `get_broker(settings)` passes the gate; otherwise it raises and names
   what is missing.

See `demo_robot.py` for a robot that uses ONLY the contract + registry and works
across finam/quik by config.
```
