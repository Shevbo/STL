# M4 Instrument Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать загрузку справочника инструментов Finam Trade API, поиск по тикеру, кэш в памяти и фиксацию MVP-символа в конфиге.

**Architecture:** `InstrumentRegistry` обёртывает три эндпоинта Finam AssetsService через `AsyncAuthClient`. Поиск (`/v1/assets/all`) работает постранично и кэширует весь справочник в памяти за сессию. Детали и параметры (`/v1/assets/{symbol}`) запрашиваются отдельно. CLI-скрипт `find_instrument.py` используется один раз для нахождения и фиксации MVP-символа.

**Tech Stack:** Python 3.12, httpx[http2], pydantic-settings, structlog, pytest, respx (mock HTTP)

---

## File Map

| Действие | Файл | Ответственность |
|----------|------|-----------------|
| Create | `trader/registry/__init__.py` | re-export |
| Create | `trader/registry/models.py` | Instrument, InstrumentDetail, TradingParams |
| Create | `trader/registry/client.py` | InstrumentRegistry |
| Modify | `trader/config.py` | add finam_mvp_symbol |
| Create | `tests/registry/__init__.py` | пустой |
| Create | `tests/registry/test_models.py` | unit: парсинг моделей |
| Create | `tests/registry/test_client.py` | unit: поиск, детали, параметры (respx) |
| Create | `tests/registry/test_integration.py` | integration: реальный API |
| Create | `scripts/find_instrument.py` | CLI: поиск → сохранение в конфиг |
| Create | `docs/config/MVP-instrument.md` | генерируется скриптом |

---

## Task 1: Models

**Files:**
- Create: `trader/registry/models.py`
- Create: `tests/registry/__init__.py`
- Create: `tests/registry/test_models.py`

- [ ] **Step 1.1: Создать пустой `tests/registry/__init__.py`**

```bash
mkdir -p tests/registry
touch tests/registry/__init__.py
```

- [ ] **Step 1.2: Написать тесты для моделей**

Создать `tests/registry/test_models.py`:

```python
from decimal import Decimal
from datetime import date

from trader.registry.models import Instrument, InstrumentDetail, TradingParams


def test_instrument_symbol_property():
    inst = Instrument(symbol="GZM6@RFUD", ticker="GZM6", mic="RFUD",
                      name="Газпром-6.26", type="future", is_archived=False)
    assert inst.symbol == "GZM6@RFUD"
    assert inst.ticker == "GZM6"
    assert inst.mic == "RFUD"


def test_instrument_detail_min_step_calculation():
    # min_step=10, decimals=2 → actual step = 10 / 10^2 = 0.10
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
```

- [ ] **Step 1.3: Запустить тесты — убедиться что падают**

```bash
cd "/home/shectory/workspaces/Shectory Trade & Lab"
poetry run pytest tests/registry/test_models.py -v
```

Ожидаем: `ImportError` — модуль ещё не создан.

- [ ] **Step 1.4: Создать `trader/registry/models.py`**

```python
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class Instrument:
    symbol: str
    ticker: str
    mic: str
    name: str
    type: str
    is_archived: bool


@dataclass
class InstrumentDetail(Instrument):
    lot_size: Decimal
    min_step: Decimal
    expiration_date: date | None
    quote_currency: str


@dataclass
class TradingParams:
    symbol: str
    is_tradable: bool
    long_initial_margin: Decimal
    short_initial_margin: Decimal
```

- [ ] **Step 1.5: Создать `trader/registry/__init__.py`**

```python
from trader.registry.client import InstrumentRegistry
from trader.registry.models import Instrument, InstrumentDetail, TradingParams

__all__ = ["InstrumentRegistry", "Instrument", "InstrumentDetail", "TradingParams"]
```

(Файл `client.py` ещё не существует — `__init__.py` создадим пустым сейчас, импорты добавим после Task 2.)

Создать пустой `trader/registry/__init__.py`:

```python
```

- [ ] **Step 1.6: Запустить тесты — убедиться что проходят**

```bash
poetry run pytest tests/registry/test_models.py -v
```

Ожидаем: 3 PASSED.

- [ ] **Step 1.7: Коммит**

```bash
rtk git add trader/registry/__init__.py trader/registry/models.py tests/registry/__init__.py tests/registry/test_models.py
git commit -m "feat(M4): Instrument, InstrumentDetail, TradingParams models"
```

---

## Task 2: InstrumentRegistry — search с пагинацией

**Files:**
- Create: `trader/registry/client.py`
- Create: `tests/registry/test_client.py`

Finam `/v1/assets/all` response format:
```json
{
  "assets": [
    {"symbol": "GZM6@RFUD", "ticker": "GZM6", "mic": "RFUD",
     "name": "Газпром-6.26", "type": "future", "is_archived": false}
  ],
  "next_cursor": 12345
}
```
Пагинация: если `next_cursor == 0` или отсутствует — последняя страница.

- [ ] **Step 2.1: Написать unit тесты для `search()`**

Создать `tests/registry/test_client.py`:

```python
import pytest
import respx
import httpx
from trader.registry.client import InstrumentRegistry
from trader.registry.models import Instrument


@pytest.fixture
def registry():
    return InstrumentRegistry(
        base_url="https://api.finam.ru",
        get_token=lambda: "test_token",
    )


PAGE_1 = {
    "assets": [
        {"symbol": "GZM6@RFUD", "ticker": "GZM6", "mic": "RFUD",
         "name": "Газпром-6.26", "type": "future", "is_archived": False},
        {"symbol": "SBER@MISX", "ticker": "SBER", "mic": "MISX",
         "name": "Сбербанк", "type": "stock", "is_archived": False},
    ],
    "next_cursor": 999,
}

PAGE_2 = {
    "assets": [
        {"symbol": "GZU6@RFUD", "ticker": "GZU6", "mic": "RFUD",
         "name": "Газпром-9.26", "type": "future", "is_archived": False},
    ],
    "next_cursor": 0,
}


@respx.mock
async def test_search_paginates_and_filters(registry):
    respx.get("https://api.finam.ru/v1/assets/all").mock(
        side_effect=[
            httpx.Response(200, json=PAGE_1),
            httpx.Response(200, json=PAGE_2),
        ]
    )
    results = await registry.search("GZM6")
    assert len(results) == 1
    assert results[0].symbol == "GZM6@RFUD"
    assert results[0].ticker == "GZM6"


@respx.mock
async def test_search_empty_result(registry):
    respx.get("https://api.finam.ru/v1/assets/all").mock(
        return_value=httpx.Response(200, json={"assets": [], "next_cursor": 0})
    )
    results = await registry.search("UNKNOWN")
    assert results == []


@respx.mock
async def test_search_uses_cache_on_second_call(registry):
    route = respx.get("https://api.finam.ru/v1/assets/all").mock(
        return_value=httpx.Response(200, json={"assets": PAGE_1["assets"], "next_cursor": 0})
    )
    await registry.search("GZM6")
    await registry.search("SBER")
    # Второй вызов должен использовать кэш — HTTP вызывается только один раз
    assert route.call_count == 1
```

- [ ] **Step 2.2: Запустить — убедиться что падают**

```bash
poetry run pytest tests/registry/test_client.py -v
```

Ожидаем: `ImportError` — `client.py` не существует.

- [ ] **Step 2.3: Создать `trader/registry/client.py` с `search()`**

```python
import structlog
from typing import Callable, Awaitable
import httpx

from trader.registry.models import Instrument, InstrumentDetail, TradingParams

log = structlog.get_logger()

_ASSETS_ALL_PATH = "/v1/assets/all"
_ASSET_DETAIL_PATH = "/v1/assets/{symbol}"
_ASSET_PARAMS_PATH = "/v1/assets/{symbol}/params"


class InstrumentRegistry:
    def __init__(self, base_url: str, get_token: Callable[[], str | Awaitable[str]]):
        self._base_url = base_url
        self._get_token = get_token
        self._cache: dict[str, Instrument] | None = None
        self._http = httpx.AsyncClient(http2=True, base_url=base_url)

    async def _token(self) -> str:
        result = self._get_token()
        if hasattr(result, "__await__"):
            return await result
        return result

    async def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {await self._token()}"}

    def _parse_instrument(self, data: dict) -> Instrument:
        return Instrument(
            symbol=data["symbol"],
            ticker=data["ticker"],
            mic=data["mic"],
            name=data["name"],
            type=data.get("type", ""),
            is_archived=data.get("is_archived", False),
        )

    async def _load_all(self) -> dict[str, Instrument]:
        cache: dict[str, Instrument] = {}
        cursor = 0
        headers = await self._auth_headers()
        while True:
            params = {"only_active": "true", "cursor": str(cursor)}
            response = await self._http.get(
                _ASSETS_ALL_PATH, headers=headers, params=params
            )
            response.raise_for_status()
            body = response.json()
            for item in body.get("assets", []):
                inst = self._parse_instrument(item)
                cache[inst.symbol] = inst
            next_cursor = body.get("next_cursor", 0)
            if not next_cursor:
                break
            cursor = next_cursor
        log.info("registry.loaded", count=len(cache))
        return cache

    async def search(self, ticker: str) -> list[Instrument]:
        if self._cache is None:
            self._cache = await self._load_all()
        return [
            inst for inst in self._cache.values()
            if inst.ticker == ticker
        ]

    async def aclose(self):
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
```

- [ ] **Step 2.4: Запустить — убедиться что проходят**

```bash
poetry run pytest tests/registry/test_client.py -v
```

Ожидаем: 3 PASSED.

- [ ] **Step 2.5: Коммит**

```bash
rtk git add trader/registry/client.py tests/registry/test_client.py
git commit -m "feat(M4): InstrumentRegistry.search() with pagination and cache"
```

---

## Task 3: get_detail() — лот, шаг, экспирация

**Files:**
- Modify: `trader/registry/client.py`
- Modify: `tests/registry/test_client.py`

Finam `GET /v1/assets/{symbol}?account_id=N` response:
```json
{
  "ticker": "GZM6", "mic": "RFUD", "name": "Газпром-6.26",
  "type": "future", "is_archived": false,
  "decimals": 0, "min_step": 10,
  "lot_size": {"value": "1"},
  "quote_currency": "RUB",
  "future_details": {
    "expiration_date": "2026-06-19T00:00:00Z",
    "contract_size": {"value": "100"}
  }
}
```

Формула шага цены: `min_step_actual = min_step / 10^decimals`.
`lot_size` и `contract_size` — объект `{"value": "N"}` (google.type.Decimal).
`expiration_date` — ISO timestamp строка (из `future_details`).

- [ ] **Step 3.1: Добавить тест для `get_detail()`**

Добавить в `tests/registry/test_client.py`:

```python
from decimal import Decimal
from datetime import date, timezone, datetime

DETAIL_RESPONSE = {
    "ticker": "GZM6", "mic": "RFUD", "name": "Газпром-6.26",
    "type": "future", "is_archived": False,
    "decimals": 0, "min_step": 10,
    "lot_size": {"value": "1"},
    "quote_currency": "RUB",
    "future_details": {
        "expiration_date": "2026-06-19T00:00:00Z",
        "contract_size": {"value": "100"},
    },
}


@respx.mock
async def test_get_detail_parses_response(registry):
    respx.get("https://api.finam.ru/v1/assets/GZM6@RFUD").mock(
        return_value=httpx.Response(200, json=DETAIL_RESPONSE)
    )
    detail = await registry.get_detail("GZM6@RFUD", account_id="2035452")
    assert detail.symbol == "GZM6@RFUD"
    assert detail.ticker == "GZM6"
    assert detail.mic == "RFUD"
    assert detail.lot_size == Decimal("1")
    assert detail.min_step == Decimal("10")  # 10 / 10^0 = 10
    assert detail.expiration_date == date(2026, 6, 19)
    assert detail.quote_currency == "RUB"
```

- [ ] **Step 3.2: Запустить — убедиться что падает**

```bash
poetry run pytest tests/registry/test_client.py::test_get_detail_parses_response -v
```

Ожидаем: FAILED — `get_detail` не существует.

- [ ] **Step 3.3: Добавить `get_detail()` в `trader/registry/client.py`**

Добавить после метода `search()`:

```python
    async def get_detail(self, symbol: str, account_id: str) -> InstrumentDetail:
        from decimal import Decimal
        from datetime import datetime, date

        headers = await self._auth_headers()
        path = _ASSET_DETAIL_PATH.format(symbol=symbol)
        response = await self._http.get(
            path, headers=headers, params={"account_id": account_id}
        )
        response.raise_for_status()
        data = response.json()

        decimals = data.get("decimals", 0)
        raw_min_step = data.get("min_step", 0)
        min_step = Decimal(str(raw_min_step)) / (Decimal("10") ** decimals)

        lot_size = Decimal(data.get("lot_size", {}).get("value", "1"))

        expiration_date: date | None = None
        future_details = data.get("future_details")
        if future_details and future_details.get("expiration_date"):
            expiration_date = datetime.fromisoformat(
                future_details["expiration_date"].replace("Z", "+00:00")
            ).date()

        return InstrumentDetail(
            symbol=symbol,
            ticker=data.get("ticker", ""),
            mic=data.get("mic", ""),
            name=data.get("name", ""),
            type=data.get("type", ""),
            is_archived=data.get("is_archived", False),
            lot_size=lot_size,
            min_step=min_step,
            expiration_date=expiration_date,
            quote_currency=data.get("quote_currency", ""),
        )
```

- [ ] **Step 3.4: Запустить — убедиться что проходит**

```bash
poetry run pytest tests/registry/test_client.py -v
```

Ожидаем: 4 PASSED.

- [ ] **Step 3.5: Коммит**

```bash
rtk git add trader/registry/client.py tests/registry/test_client.py
git commit -m "feat(M4): InstrumentRegistry.get_detail()"
```

---

## Task 4: get_params() — ГО и is_tradable

**Files:**
- Modify: `trader/registry/client.py`
- Modify: `tests/registry/test_client.py`

Finam `GET /v1/assets/{symbol}/params?account_id=N` response:
```json
{
  "symbol": "GZM6@RFUD",
  "account_id": "2035452",
  "is_tradable": {"value": true},
  "long_initial_margin": {"currency_code": "RUB", "units": "5000", "nanos": 0},
  "short_initial_margin": {"currency_code": "RUB", "units": "5000", "nanos": 0}
}
```

`is_tradable` — `google.protobuf.BoolValue`: объект `{"value": true}` или `null`.
`long_initial_margin` — `google.type.Money`: `units` (целая часть) + `nanos` (дробная, /10^9).

- [ ] **Step 4.1: Добавить тест для `get_params()`**

Добавить в `tests/registry/test_client.py`:

```python
PARAMS_RESPONSE = {
    "symbol": "GZM6@RFUD",
    "account_id": "2035452",
    "is_tradable": {"value": True},
    "long_initial_margin": {"currency_code": "RUB", "units": "5000", "nanos": 0},
    "short_initial_margin": {"currency_code": "RUB", "units": "4800", "nanos": 500000000},
}


@respx.mock
async def test_get_params_parses_tradable_and_margins(registry):
    respx.get("https://api.finam.ru/v1/assets/GZM6@RFUD/params").mock(
        return_value=httpx.Response(200, json=PARAMS_RESPONSE)
    )
    params = await registry.get_params("GZM6@RFUD", account_id="2035452")
    assert params.symbol == "GZM6@RFUD"
    assert params.is_tradable is True
    assert params.long_initial_margin == Decimal("5000")
    assert params.short_initial_margin == Decimal("4800.5")


@respx.mock
async def test_get_params_not_tradable_when_null(registry):
    respx.get("https://api.finam.ru/v1/assets/GZM6@RFUD/params").mock(
        return_value=httpx.Response(200, json={
            "symbol": "GZM6@RFUD",
            "is_tradable": None,
            "long_initial_margin": {"currency_code": "RUB", "units": "0", "nanos": 0},
            "short_initial_margin": {"currency_code": "RUB", "units": "0", "nanos": 0},
        })
    )
    params = await registry.get_params("GZM6@RFUD", account_id="2035452")
    assert params.is_tradable is False
```

- [ ] **Step 4.2: Запустить — убедиться что падают**

```bash
poetry run pytest tests/registry/test_client.py::test_get_params_parses_tradable_and_margins tests/registry/test_client.py::test_get_params_not_tradable_when_null -v
```

Ожидаем: FAILED — `get_params` не существует.

- [ ] **Step 4.3: Добавить `get_params()` в `trader/registry/client.py`**

Добавить в начале файла после существующих импортов:
```python
from decimal import Decimal
```

Добавить метод после `get_detail()`:

```python
    async def get_params(self, symbol: str, account_id: str) -> TradingParams:
        headers = await self._auth_headers()
        path = _ASSET_PARAMS_PATH.format(symbol=symbol)
        response = await self._http.get(
            path, headers=headers, params={"account_id": account_id}
        )
        response.raise_for_status()
        data = response.json()

        is_tradable_obj = data.get("is_tradable")
        is_tradable = bool(is_tradable_obj.get("value")) if is_tradable_obj else False

        def parse_money(obj: dict | None) -> Decimal:
            if not obj:
                return Decimal("0")
            units = Decimal(str(obj.get("units", "0")))
            nanos = Decimal(str(obj.get("nanos", 0))) / Decimal("1000000000")
            return units + nanos

        return TradingParams(
            symbol=symbol,
            is_tradable=is_tradable,
            long_initial_margin=parse_money(data.get("long_initial_margin")),
            short_initial_margin=parse_money(data.get("short_initial_margin")),
        )
```

- [ ] **Step 4.4: Запустить все unit тесты**

```bash
poetry run pytest tests/registry/test_client.py tests/registry/test_models.py -v
```

Ожидаем: 8 PASSED.

- [ ] **Step 4.5: Обновить `trader/registry/__init__.py`**

```python
from trader.registry.client import InstrumentRegistry
from trader.registry.models import Instrument, InstrumentDetail, TradingParams

__all__ = ["InstrumentRegistry", "Instrument", "InstrumentDetail", "TradingParams"]
```

- [ ] **Step 4.6: Запустить все тесты проекта**

```bash
poetry run pytest -m "not integration" -v
```

Ожидаем: все PASSED (8 старых + 8 новых = 16 PASSED).

- [ ] **Step 4.7: Коммит**

```bash
rtk git add trader/registry/ tests/registry/
git commit -m "feat(M4): InstrumentRegistry.get_params(), full unit test suite"
```

---

## Task 5: Config — finam_mvp_symbol

**Files:**
- Modify: `trader/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 5.1: Добавить поле в Settings**

В `trader/config.py` добавить поле после `finam_account_id`:

```python
    finam_mvp_symbol: str = ""
```

- [ ] **Step 5.2: Добавить тест**

В `tests/test_config.py` добавить:

```python
def test_settings_mvp_symbol_default_empty(monkeypatch):
    monkeypatch.setenv("FINAM_SECRET_TOKEN", "test_secret")
    settings = Settings(_env_file=[])
    assert settings.finam_mvp_symbol == ""


def test_settings_mvp_symbol_from_env(monkeypatch):
    monkeypatch.setenv("FINAM_SECRET_TOKEN", "test_secret")
    monkeypatch.setenv("FINAM_MVP_SYMBOL", "GZM6@RFUD")
    settings = Settings(_env_file=[])
    assert settings.finam_mvp_symbol == "GZM6@RFUD"
```

- [ ] **Step 5.3: Запустить тесты**

```bash
poetry run pytest tests/test_config.py -v
```

Ожидаем: все PASSED.

- [ ] **Step 5.4: Коммит**

```bash
rtk git add trader/config.py tests/test_config.py
git commit -m "feat(M4): add finam_mvp_symbol to Settings"
```

---

## Task 6: Integration tests

**Files:**
- Create: `tests/registry/test_integration.py`

- [ ] **Step 6.1: Создать `tests/registry/test_integration.py`**

```python
"""
Integration tests — requires real Finam credentials and network.

Run:
  HTTPS_PROXY="" HTTP_PROXY="" poetry run pytest tests/registry/test_integration.py -v -m integration --tb=no
"""
import pytest
from trader.config import Settings
from trader.auth.client import AsyncAuthClient
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
    # Ищем по известному тикеру — список не должен быть пустым
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
```

- [ ] **Step 6.2: Запустить integration тесты (только `test_search_returns_nonempty_list`)**

```bash
HTTPS_PROXY="" HTTP_PROXY="" poetry run pytest tests/registry/test_integration.py::test_search_returns_nonempty_list -v -m integration --tb=no
```

Ожидаем: PASSED.

- [ ] **Step 6.3: Коммит**

```bash
rtk git add tests/registry/test_integration.py
git commit -m "feat(M4): integration tests for InstrumentRegistry"
```

---

## Task 7: CLI-скрипт find_instrument.py

**Files:**
- Create: `scripts/find_instrument.py`

Скрипт ищет инструмент по тикеру, показывает результаты, сохраняет символ в `~/.shectory_trade.env` и создаёт `docs/config/MVP-instrument.md`.

- [ ] **Step 7.1: Создать `scripts/find_instrument.py`**

```python
#!/usr/bin/env python3
"""
Поиск инструмента в справочнике Finam и фиксация MVP-символа.

Usage:
    HTTPS_PROXY="" HTTP_PROXY="" poetry run python scripts/find_instrument.py --ticker GZM6
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# Добавляем корень проекта в PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from trader.auth.client import AsyncAuthClient
from trader.config import Settings
from trader.registry.client import InstrumentRegistry


async def main(ticker: str) -> None:
    settings = Settings()

    async with AsyncAuthClient(
        base_url=settings.finam_api_base_url,
        secret_token=settings.finam_secret_token.get_secret_value(),
        refresh_before_secs=settings.finam_token_refresh_before_secs,
    ) as auth:
        async with InstrumentRegistry(
            base_url=settings.finam_api_base_url,
            get_token=auth.get_token,
        ) as reg:
            print(f"Ищу инструменты с тикером: {ticker}")
            results = await reg.search(ticker)

            if not results:
                print(f"Инструменты с тикером '{ticker}' не найдены.")
                return

            print(f"\nНайдено: {len(results)}\n")
            print(f"{'#':<3} {'Symbol':<20} {'Name':<30} {'Type':<10} {'Archived'}")
            print("-" * 75)
            for i, inst in enumerate(results):
                print(f"{i:<3} {inst.symbol:<20} {inst.name:<30} {inst.type:<10} {inst.is_archived}")

            print()

            # Фильтруем только активные
            active = [r for r in results if not r.is_archived]
            if not active:
                print("Все найденные инструменты архивные.")
                return

            # Если только один активный — берём его автоматически
            if len(active) == 1:
                chosen = active[0]
                print(f"Единственный активный инструмент: {chosen.symbol}")
            else:
                idx = input(f"Введите номер инструмента (0-{len(results)-1}): ")
                chosen = results[int(idx)]

            print(f"\nЗагружаю детали для {chosen.symbol}...")
            detail = await reg.get_detail(chosen.symbol, account_id=settings.finam_account_id)
            params = await reg.get_params(chosen.symbol, account_id=settings.finam_account_id)

            print(f"  Лот:         {detail.lot_size}")
            print(f"  Шаг цены:    {detail.min_step}")
            print(f"  Экспирация:  {detail.expiration_date}")
            print(f"  Валюта:      {detail.quote_currency}")
            print(f"  Доступен:    {params.is_tradable}")
            print(f"  ГО лонг:     {params.long_initial_margin} {detail.quote_currency}")
            print(f"  ГО шорт:     {params.short_initial_margin} {detail.quote_currency}")

            if not params.is_tradable:
                print(f"\nВНИМАНИЕ: инструмент {chosen.symbol} недоступен для торговли на счёте {settings.finam_account_id}.")

            # Сохраняем в ~/.shectory_trade.env
            env_path = Path.home() / ".shectory_trade.env"
            lines = env_path.read_text().splitlines() if env_path.exists() else []
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("FINAM_MVP_SYMBOL="):
                    new_lines.append(f"FINAM_MVP_SYMBOL={chosen.symbol}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"FINAM_MVP_SYMBOL={chosen.symbol}")
            env_path.write_text("\n".join(new_lines) + "\n")
            print(f"\nСохранено в {env_path}: FINAM_MVP_SYMBOL={chosen.symbol}")

            # Создаём docs/config/MVP-instrument.md
            docs_path = Path(__file__).parent.parent / "docs" / "config" / "MVP-instrument.md"
            docs_path.parent.mkdir(parents=True, exist_ok=True)
            docs_path.write_text(f"""# MVP Instrument

**Symbol:** `{chosen.symbol}`  
**Ticker:** {detail.ticker}  
**MIC:** {detail.mic}  
**Name:** {detail.name}  
**Type:** {detail.type}  
**Expiration:** {detail.expiration_date}  

## Trading Parameters

| Parameter | Value |
|-----------|-------|
| Lot size | {detail.lot_size} |
| Min price step | {detail.min_step} |
| Quote currency | {detail.quote_currency} |
| Is tradable | {params.is_tradable} |
| Long initial margin (GO) | {params.long_initial_margin} {detail.quote_currency} |
| Short initial margin (GO) | {params.short_initial_margin} {detail.quote_currency} |

## Why This Symbol

Выбран как MVP-инструмент для торговой системы Shectory Trader.  
Дата выбора: {datetime.now().strftime('%Y-%m-%d')}  
Счёт: {settings.finam_account_id}
""")
            print(f"Создан файл: {docs_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Поиск инструмента Finam")
    parser.add_argument("--ticker", required=True, help="Тикер инструмента (например: GZM6)")
    args = parser.parse_args()
    asyncio.run(main(args.ticker))
```

- [ ] **Step 7.2: Запустить скрипт**

```bash
HTTPS_PROXY="" HTTP_PROXY="" poetry run python scripts/find_instrument.py --ticker GZM6
```

Скрипт покажет список найденных инструментов. Выбери нужный (или он выберется автоматически если один активный).

После выполнения:
- `~/.shectory_trade.env` будет содержать `FINAM_MVP_SYMBOL=GZM6@RFUD` (или аналогичный)
- `docs/config/MVP-instrument.md` будет создан

- [ ] **Step 7.3: Запустить integration тесты для MVP-символа**

```bash
HTTPS_PROXY="" HTTP_PROXY="" poetry run pytest tests/registry/test_integration.py -v -m integration --tb=no
```

Ожидаем: 3 PASSED (в т.ч. ранее skipped тесты для MVP-символа).

- [ ] **Step 7.4: Запустить все unit тесты — убедиться ничего не сломалось**

```bash
poetry run pytest -m "not integration" -v
```

Ожидаем: 18 PASSED.

- [ ] **Step 7.5: Коммит**

```bash
rtk git add scripts/find_instrument.py docs/config/MVP-instrument.md
git commit -m "feat(M4): find_instrument.py CLI + MVP-instrument.md"
rtk git push
```

---

## Done Criteria

- [ ] `poetry run pytest -m "not integration"` → все зелёные (18 тестов)
- [ ] `HTTPS_PROXY="" HTTP_PROXY="" poetry run pytest -m integration --tb=no` → все зелёные (5 тестов)
- [ ] `~/.shectory_trade.env` содержит `FINAM_MVP_SYMBOL=<symbol>`
- [ ] `docs/config/MVP-instrument.md` создан с деталями инструмента
- [ ] Код запушен на GitHub
