from decimal import Decimal

from trader.registry.models import Instrument, InstrumentDetail, TradingParams


def test_instrument_symbol_property():
    inst = Instrument(symbol="GZM6@RFUD", ticker="GZM6", mic="RFUD",
                      name="Газпром-6.26", type="future", is_archived=False)
    assert inst.symbol == "GZM6@RFUD"
    assert inst.ticker == "GZM6"
    assert inst.mic == "RFUD"


def test_instrument_detail_min_step_calculation():
    detail = InstrumentDetail(
        symbol="TEST@MISX", ticker="TEST", mic="MISX",
        name="Test", type="stock", is_archived=False,
        lot_size=Decimal("10"),
        min_step=Decimal("0.10"),
        expiration_date=None,
        quote_currency="RUB",
    )
    assert detail.min_step == Decimal("0.10")
    assert detail.lot_size == Decimal("10")


def test_trading_params_is_tradable():
    params = TradingParams(
        symbol="GZM6@RFUD",
        is_tradable=True,
        long_initial_margin=Decimal("5000"),
        short_initial_margin=Decimal("5000"),
    )
    assert params.is_tradable is True
    assert params.long_initial_margin == Decimal("5000")
