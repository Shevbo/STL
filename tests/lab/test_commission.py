from trader.lab.commission import (
    BROKER_FEE_PER_CONTRACT,
    MOEX_TAKER_RATE,
    commission_for,
    fee_group,
)


def test_fee_group_mapping():
    assert fee_group("RIM6") == "index"
    assert fee_group("RTS-6.26") == "index"
    assert fee_group("SiM6") == "fx"
    assert fee_group("GZM6") == "stock"
    assert fee_group("BRN6") == "commodity"
    assert fee_group("ZZZ9") == "index"   # unknown → conservative index default


def test_maker_is_broker_only():
    # Maker (limit in book): no exchange fee, just the flat broker fee per contract.
    fee = commission_for("RIM6", price=100_000.0, qty=3, point_value=1.0, taker=False)
    assert fee == BROKER_FEE_PER_CONTRACT * 3


def test_taker_adds_exchange_fee_on_notional():
    price, qty, pv = 100_000.0, 2, 1.0
    fee = commission_for("RIM6", price, qty, pv, taker=True)
    broker = BROKER_FEE_PER_CONTRACT * qty
    exchange = MOEX_TAKER_RATE["index"] * price * pv * qty
    assert fee == broker + exchange
    # Taker must always cost more than maker.
    assert fee > commission_for("RIM6", price, qty, pv, taker=False)


def test_group_rates_differ():
    price, pv = 100_000.0, 1.0
    si = commission_for("SiM6", price, 1, pv, taker=True)
    ri = commission_for("RIM6", price, 1, pv, taker=True)
    gz = commission_for("GZM6", price, 1, pv, taker=True)
    # fx < index < stock taker rates → ordering of total fee on equal notional.
    assert si < ri < gz
