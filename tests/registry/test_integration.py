"""
Integration tests — requires real Finam credentials and network.

Run:
  HTTPS_PROXY="" HTTP_PROXY="" poetry run pytest tests/registry/test_integration.py -v -m integration --tb=no
"""
import pytest

from trader.auth.client import AsyncAuthClient
from trader.config import Settings
from trader.registry.client import InstrumentRegistry

pytestmark = pytest.mark.integration


@pytest.fixture
async def registry():
    settings = Settings()
    auth = AsyncAuthClient(
        base_url=settings.finam_api_base_url,
        secret_token=settings.finam_secret_token.get_secret_value(),
        refresh_before_secs=settings.finam_token_refresh_before_secs,
    )
    reg = InstrumentRegistry(
        base_url=settings.finam_api_base_url,
        get_token=auth.get_token,
    )
    yield reg
    await auth.aclose()
    await reg.aclose()


async def test_search_returns_nonempty_list(registry):
    results = await registry.search("SBER")
    assert len(results) > 0
    assert all(r.ticker == "SBER" for r in results)


async def test_get_detail_mvp_symbol(registry):
    settings = Settings()
    if not settings.finam_mvp_symbol:
        pytest.skip("FINAM_MVP_SYMBOL not set — run find_instrument.py first")
    detail = await registry.get_detail(
        settings.finam_mvp_symbol, account_id=settings.finam_account_id
    )
    assert detail.symbol == settings.finam_mvp_symbol
    assert detail.lot_size > 0
    assert detail.min_step > 0


async def test_get_params_mvp_symbol_is_tradable(registry):
    settings = Settings()
    if not settings.finam_mvp_symbol:
        pytest.skip("FINAM_MVP_SYMBOL not set — run find_instrument.py first")
    params = await registry.get_params(
        settings.finam_mvp_symbol, account_id=settings.finam_account_id
    )
    assert params.is_tradable is True
