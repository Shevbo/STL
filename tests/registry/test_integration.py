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


async def test_assets_api_returns_nonempty(registry):
    """Verify /v1/assets/all is accessible and returns parseable instruments.

    Full search() loads thousands of pages (~15+ min). This test fetches
    the first page only — enough to confirm auth and API connectivity.
    """
    headers = await registry._auth_headers()
    response = await registry._get_page(headers, cursor=0)
    body = response.json()
    assets = body.get("assets", [])
    assert len(assets) > 0
    for asset in assets[:3]:
        inst = registry._parse_instrument(asset)
        assert inst.symbol
        assert inst.ticker


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
