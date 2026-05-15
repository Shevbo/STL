# M4 Instrument Registry — Design Spec

**Date:** 2026-05-15  
**Stage:** Этап 3 ТЗ  
**Status:** Approved

---

## Goal

Загрузка справочника инструментов Finam, поиск `symbol@mic` по тикеру, кэш в памяти, однократная фиксация MVP-символа в конфиге.

---

## Architecture

```
trader/registry/
├── __init__.py
├── models.py       # Instrument, InstrumentDetail, TradingParams
└── client.py       # InstrumentRegistry

scripts/
└── find_instrument.py   # CLI: поиск тикера → сохранение в конфиг

docs/config/
└── MVP-instrument.md    # Human-readable: что выбрано и почему
```

---

## API Endpoints Used

| Method | Path | Назначение |
|--------|------|------------|
| GET | `/v1/assets/all?only_active=true&cursor=N` | Полный справочник с пагинацией |
| GET | `/v1/assets/{symbol}?account_id=N` | Детали инструмента (лот, шаг, экспирация) |
| GET | `/v1/assets/{symbol}/params?account_id=N` | Торговые параметры (ГО, доступность) |

Авторизация: `Authorization: Bearer <access_token>` (через `AsyncAuthClient`).

---

## Data Models

```python
@dataclass
class Instrument:
    symbol: str        # ticker@mic, e.g. "GZM6@RFUD"
    ticker: str
    mic: str
    name: str
    type: str          # "future", "stock", ...
    is_archived: bool

@dataclass
class InstrumentDetail(Instrument):
    lot_size: Decimal
    min_step: Decimal  # = raw_min_step / 10^decimals
    expiration_date: date | None
    quote_currency: str

@dataclass
class TradingParams:
    symbol: str
    is_tradable: bool
    long_initial_margin: Decimal   # ГО лонг (руб.)
    short_initial_margin: Decimal  # ГО шорт (руб.)
```

---

## InstrumentRegistry

```python
class InstrumentRegistry:
    def __init__(self, base_url: str, auth_client: AsyncAuthClient): ...

    async def search(self, ticker: str) -> list[Instrument]:
        """Перебирает /v1/assets/all постранично, возвращает совпадения по тикеру."""

    async def get_detail(self, symbol: str, account_id: str) -> InstrumentDetail:
        """GET /v1/assets/{symbol} — лот, шаг цены, экспирация."""

    async def get_params(self, symbol: str, account_id: str) -> TradingParams:
        """GET /v1/assets/{symbol}/params — ГО, is_tradable."""
```

- `search()` кэширует весь справочник в `_cache: dict[str, Instrument]` на время жизни процесса
- Нет хардкода тикеров — реестр полностью generic

---

## Config

`Settings` получает новое поле:

```python
finam_mvp_symbol: str = ""   # e.g. "GZM6@RFUD", заполняется скриптом
```

---

## find_instrument.py (CLI)

```bash
poetry run python scripts/find_instrument.py --ticker GZM6
```

Шаги:
1. Auth → access token
2. `registry.search("GZM6")` → список совпадений
3. Вывод таблицы (symbol, name, type, is_archived)
4. Для каждого: `get_params()` → is_tradable, ГО
5. Сохраняет выбранный символ в `~/.shectory_trade.env` (FINAM_MVP_SYMBOL)
6. Создаёт `docs/config/MVP-instrument.md` с обоснованием

---

## Tests

### Unit (no network, respx mock)

| Test | Что проверяет |
|------|---------------|
| `test_search_paginates` | Несколько страниц, собирает все результаты |
| `test_search_filters_by_ticker` | Возвращает только точное совпадение |
| `test_search_empty` | Тикер не найден → пустой список |
| `test_search_uses_cache` | Второй вызов не делает HTTP запрос |
| `test_get_detail_parses_response` | Лот, шаг (с decimals), экспирация |
| `test_get_params_parses_tradable` | is_tradable, long/short margin |

### Integration (`@pytest.mark.integration`)

| Test | Что проверяет |
|------|---------------|
| `test_search_returns_nonempty` | Реальный вызов /v1/assets/all непустой |
| `test_get_detail_mvp_symbol` | Если FINAM_MVP_SYMBOL задан — детали без ошибок |

---

## Success Criteria (из ТЗ Этап 3)

- [ ] `search(ticker)` находит инструмент из справочника Finam
- [ ] `symbol@mic` зафиксирован в `~/.shectory_trade.env` (`FINAM_MVP_SYMBOL`)
- [ ] `docs/config/MVP-instrument.md` создан (что выбрано и почему)
- [ ] `get_params()` подтверждает `is_tradable=true` для MVP-символа
- [ ] Все unit тесты зелёные, 2 integration теста проходят
