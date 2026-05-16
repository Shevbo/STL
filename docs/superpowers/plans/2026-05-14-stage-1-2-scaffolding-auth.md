# Shectory Trader — Plan 1: Scaffolding & M0 Auth

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Установить структуру проекта Python/FastAPI и реализовать модуль аутентификации M0, который получает JWT из secret-токена Finam и автоматически его обновляет.

**Architecture:** Монолитный Python-пакет `trader/` с отдельными модулями по доменам (auth, md, tx, oms...). `AsyncAuthClient` — единственная точка для JWT во всём приложении. Конфигурация через pydantic-settings из env-переменных. Все вызовы Finam API — через httpx с HTTP/2 (требование Finam).

**Tech Stack:** Python 3.12, httpx[http2], pydantic-settings 2.x, structlog, pytest, pytest-asyncio, poetry, ruff

---

## Предварительные требования (Stage 0 — ручной)

Прежде чем писать код для M0, нужен Finam secret token. **Без него интеграционные тесты не пройдут** (unit-тесты с mock работают без токена).

1. Войти в личный кабинет ФИНАМ
2. Перейти на https://api.finam.ru/tokens → создать токен
3. Запомнить `account_id` (номер счёта) из личного кабинета
4. Сохранить в `~/.shectory_trade.env` (**не** в репозиторий):
   ```
   FINAM_SECRET_TOKEN=<твой_токен>
   FINAM_ACCOUNT_ID=<account_id>
   ```
5. Проверить документацию https://api.finam.ru/docs/rest — раздел Authentication.
   **Критично:** уточнить точный endpoint обмена secret → JWT и формат запроса/ответа.
   В плане используется предположение `POST /api/v1/auth/token` — скорректировать при необходимости.

---

## Файловая структура

```
trader/
├── __init__.py              # версия пакета
├── config.py                # pydantic-settings: все env-переменные
└── auth/
    ├── __init__.py          # reexport AsyncAuthClient
    ├── models.py            # TokenResponse с методом is_expired()
    └── client.py            # AsyncAuthClient: get_token(), _fetch_token(), caching
tests/
├── conftest.py              # shared fixtures
├── test_config.py
└── auth/
    ├── __init__.py
    ├── test_models.py
    ├── test_auth_client.py   # unit тесты с mock
    └── test_auth_integration.py  # интеграционные (требуют реального токена)
.env.example
pyproject.toml
pytest.ini
CLAUDE.md
CHANGELOG.md
docs/
├── runbooks/
│   └── finam-access-checklist.md
└── superpowers/
    ├── plans/               # этот файл
    └── specs/
.github/
└── workflows/
    └── ci.yml
```

---

### Task 1: pyproject.toml, структура папок, .env.example

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `pytest.ini`
- Create: `CHANGELOG.md`

- [ ] **Шаг 1: Создать pyproject.toml**

```toml
[tool.poetry]
name = "shectory-trader"
version = "0.1.0"
description = "Execution platform for FORTS ММВБ via Finam Trade API"
authors = ["Shectory"]
readme = "README.md"

[tool.poetry.dependencies]
python = "^3.12"
httpx = {extras = ["http2"], version = "^0.27"}
pydantic-settings = "^2.3"
structlog = "^24.4"

[tool.poetry.group.dev.dependencies]
pytest = "^8.2"
pytest-asyncio = "^0.23"
respx = "^0.21"
ruff = "^0.4"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

- [ ] **Шаг 2: Создать .env.example**

```ini
# Finam Trade API — НЕ помещать реальные значения; реальные значения в ~/.shectory_trade.env
FINAM_API_BASE_URL=https://api.trade.finam.ru
FINAM_SECRET_TOKEN=your_secret_token_here
FINAM_ACCOUNT_ID=your_account_id_here
FINAM_TOKEN_REFRESH_BEFORE_SECS=60
```

- [ ] **Шаг 3: Создать pytest.ini**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
markers =
    integration: marks tests requiring real Finam credentials (deselect with '-m "not integration"')
```

- [ ] **Шаг 4: Создать CHANGELOG.md**

```markdown
# Changelog

## [Unreleased]
### Added
- Project scaffolding (Python 3.12, poetry, httpx[http2])
- M0 Auth module: AsyncAuthClient with JWT caching
- M10 Config: pydantic-settings
```

- [ ] **Шаг 5: Создать структуру пакетов**

```bash
mkdir -p trader/auth tests/auth
touch trader/__init__.py trader/auth/__init__.py tests/__init__.py tests/auth/__init__.py
```

- [ ] **Шаг 6: Установить зависимости**

```bash
cd ~/workspaces/Shectory\ Trade\ \&\ Lab
poetry install
```

Ожидаемый вывод: `Installing dependencies from lock file` без ошибок.

- [ ] **Шаг 7: Commit**

```bash
git add pyproject.toml .env.example pytest.ini CHANGELOG.md trader/ tests/
git commit -m "feat: project scaffolding (poetry, httpx, pydantic-settings)"
```

---

### Task 2: Config модуль (M10)

**Files:**
- Create: `trader/config.py`
- Create: `tests/test_config.py`

- [ ] **Шаг 1: Написать failing test**

```python
# tests/test_config.py
import pytest
from trader.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("FINAM_API_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("FINAM_SECRET_TOKEN", "test_secret_123")
    monkeypatch.setenv("FINAM_ACCOUNT_ID", "ACC_001")

    settings = Settings()

    assert settings.finam_api_base_url == "https://api.example.com"
    assert settings.finam_secret_token.get_secret_value() == "test_secret_123"
    assert settings.finam_account_id == "ACC_001"
    assert settings.finam_token_refresh_before_secs == 60  # default


def test_settings_missing_required_field_raises(monkeypatch):
    monkeypatch.delenv("FINAM_SECRET_TOKEN", raising=False)
    monkeypatch.delenv("FINAM_ACCOUNT_ID", raising=False)

    with pytest.raises(Exception):  # pydantic ValidationError
        Settings()
```

- [ ] **Шаг 2: Запустить — убедиться что FAIL**

```bash
poetry run pytest tests/test_config.py -v
```

Ожидаемый вывод: `FAILED` / `ModuleNotFoundError: No module named 'trader'`

- [ ] **Шаг 3: Реализовать `trader/config.py`**

```python
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    finam_api_base_url: str = "https://api.trade.finam.ru"
    finam_secret_token: SecretStr
    finam_account_id: str
    finam_token_refresh_before_secs: int = 60
```

- [ ] **Шаг 4: Запустить — убедиться что PASS**

```bash
poetry run pytest tests/test_config.py -v
```

Ожидаемый вывод: `2 passed`

- [ ] **Шаг 5: Commit**

```bash
git add trader/config.py tests/test_config.py
git commit -m "feat(M10): config module with pydantic-settings"
```

---

### Task 3: Auth models

**Files:**
- Create: `trader/auth/models.py`
- Create: `tests/auth/test_models.py`

- [ ] **Шаг 1: Написать failing test**

```python
# tests/auth/test_models.py
from datetime import datetime, timezone, timedelta
from trader.auth.models import TokenResponse


def test_token_expired_when_past_expiry():
    token = TokenResponse(
        access_token="tok_123",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert token.is_expired(buffer_secs=0) is True


def test_token_not_expired_when_far_future():
    token = TokenResponse(
        access_token="tok_123",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert token.is_expired(buffer_secs=0) is False


def test_token_expired_with_buffer():
    # Expires in 30 seconds, buffer is 60 → treated as expired
    token = TokenResponse(
        access_token="tok_123",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    assert token.is_expired(buffer_secs=60) is True
```

- [ ] **Шаг 2: Запустить — убедиться что FAIL**

```bash
poetry run pytest tests/auth/test_models.py -v
```

Ожидаемый вывод: `ImportError: cannot import name 'TokenResponse'`

- [ ] **Шаг 3: Реализовать `trader/auth/models.py`**

```python
from datetime import datetime, timezone
from pydantic import BaseModel


class TokenResponse(BaseModel):
    access_token: str
    expires_at: datetime

    def is_expired(self, buffer_secs: int = 60) -> bool:
        now = datetime.now(timezone.utc)
        return (self.expires_at - now).total_seconds() < buffer_secs
```

- [ ] **Шаг 4: Запустить — убедиться что PASS**

```bash
poetry run pytest tests/auth/test_models.py -v
```

Ожидаемый вывод: `3 passed`

- [ ] **Шаг 5: Commit**

```bash
git add trader/auth/models.py tests/auth/test_models.py
git commit -m "feat(M0): TokenResponse model with is_expired()"
```

---

### Task 4: AsyncAuthClient — unit тесты с mock

**Files:**
- Create: `tests/conftest.py`
- Create: `trader/auth/client.py`
- Modify: `trader/auth/__init__.py`
- Create: `tests/auth/test_auth_client.py`

> **⚠️ До реализации:** уточнить endpoint и формат ответа из https://api.finam.ru/docs/rest.
> В плане: `POST /api/v1/auth/token`, тело `{"secret_token": "..."}`, ответ `{"access_token": "...", "expires_at": "ISO8601"}`.
> Скорректировать `_TOKEN_PATH` и `_fetch_token()` если Finam использует другой формат.

- [ ] **Шаг 1: Создать `tests/conftest.py`**

```python
# tests/conftest.py
import pytest
from datetime import datetime, timezone, timedelta
from trader.auth.models import TokenResponse


@pytest.fixture
def mock_token():
    return TokenResponse(
        access_token="mock_jwt_token_abc123",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.fixture
def expired_token():
    return TokenResponse(
        access_token="expired_jwt_token",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
```

- [ ] **Шаг 2: Написать failing unit тесты**

```python
# tests/auth/test_auth_client.py
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone, timedelta
from trader.auth.client import AsyncAuthClient
from trader.auth.models import TokenResponse


@pytest.fixture
def auth_client():
    return AsyncAuthClient(
        base_url="https://api.example.com",
        secret_token="test_secret",
        refresh_before_secs=60,
    )


async def test_get_token_calls_fetch_on_first_call(auth_client, mock_token):
    with patch.object(auth_client, "_fetch_token", return_value=mock_token) as mock_fetch:
        token = await auth_client.get_token()

    mock_fetch.assert_called_once()
    assert token == "mock_jwt_token_abc123"


async def test_cached_token_is_reused(auth_client, mock_token):
    auth_client._cached_token = mock_token

    with patch.object(auth_client, "_fetch_token") as mock_fetch:
        token = await auth_client.get_token()

    mock_fetch.assert_not_called()
    assert token == mock_token.access_token


async def test_expired_token_triggers_refresh(auth_client, expired_token):
    auth_client._cached_token = expired_token
    new_token = TokenResponse(
        access_token="new_jwt_xyz",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    with patch.object(auth_client, "_fetch_token", return_value=new_token) as mock_fetch:
        token = await auth_client.get_token()

    mock_fetch.assert_called_once()
    assert token == "new_jwt_xyz"
```

- [ ] **Шаг 3: Запустить — убедиться что FAIL**

```bash
poetry run pytest tests/auth/test_auth_client.py -v
```

Ожидаемый вывод: `ImportError: cannot import name 'AsyncAuthClient'`

- [ ] **Шаг 4: Реализовать `trader/auth/client.py`**

```python
import httpx
import structlog
from trader.auth.models import TokenResponse

log = structlog.get_logger()

# TODO: Verify exact path from https://api.finam.ru/docs/rest → Authentication
_TOKEN_PATH = "/api/v1/auth/token"


class AsyncAuthClient:
    def __init__(self, base_url: str, secret_token: str, refresh_before_secs: int = 60):
        self._base_url = base_url
        self._secret_token = secret_token
        self._refresh_before_secs = refresh_before_secs
        self._cached_token: TokenResponse | None = None
        self._http = httpx.AsyncClient(http2=True, base_url=base_url)

    async def get_token(self) -> str:
        if self._cached_token and not self._cached_token.is_expired(self._refresh_before_secs):
            return self._cached_token.access_token
        self._cached_token = await self._fetch_token()
        return self._cached_token.access_token

    async def _fetch_token(self) -> TokenResponse:
        log.info("auth.fetch_token", base_url=self._base_url)
        response = await self._http.post(
            _TOKEN_PATH,
            json={"secret_token": self._secret_token},
        )
        response.raise_for_status()
        data = response.json()
        return TokenResponse(
            access_token=data["access_token"],
            expires_at=data["expires_at"],
        )

    async def aclose(self):
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
```

- [ ] **Шаг 5: Обновить `trader/auth/__init__.py`**

```python
from trader.auth.client import AsyncAuthClient

__all__ = ["AsyncAuthClient"]
```

- [ ] **Шаг 6: Запустить unit тесты — убедиться что PASS**

```bash
poetry run pytest tests/auth/test_auth_client.py -v
```

Ожидаемый вывод: `3 passed`

- [ ] **Шаг 7: Прогнать все unit тесты**

```bash
poetry run pytest -m "not integration" -v
```

Ожидаемый вывод: `8 passed` (2 config + 3 models + 3 auth)

- [ ] **Шаг 8: Commit**

```bash
git add trader/auth/client.py trader/auth/__init__.py tests/auth/test_auth_client.py tests/conftest.py
git commit -m "feat(M0): AsyncAuthClient with JWT caching and auto-refresh"
```

---

### Task 5: Интеграционный тест + runbook

**Files:**
- Create: `tests/auth/test_auth_integration.py`
- Create: `docs/runbooks/finam-access-checklist.md`

- [ ] **Шаг 1: Написать интеграционный тест**

```python
# tests/auth/test_auth_integration.py
"""
Integration tests — requires real Finam credentials.

Setup:
  cp .env.example .env
  # Edit .env with real FINAM_SECRET_TOKEN and FINAM_ACCOUNT_ID

Run:
  poetry run pytest tests/auth/test_auth_integration.py -v -m integration
"""
import pytest
from trader.config import Settings
from trader.auth.client import AsyncAuthClient

pytestmark = pytest.mark.integration


async def test_real_auth_returns_jwt():
    settings = Settings()

    async with AsyncAuthClient(
        base_url=settings.finam_api_base_url,
        secret_token=settings.finam_secret_token.get_secret_value(),
        refresh_before_secs=settings.finam_token_refresh_before_secs,
    ) as client:
        token = await client.get_token()

    assert token
    assert len(token) > 10


async def test_second_call_uses_cache():
    settings = Settings()

    async with AsyncAuthClient(
        base_url=settings.finam_api_base_url,
        secret_token=settings.finam_secret_token.get_secret_value(),
        refresh_before_secs=settings.finam_token_refresh_before_secs,
    ) as client:
        token1 = await client.get_token()
        token2 = await client.get_token()  # должен использовать cache

    assert token1 == token2
```

- [ ] **Шаг 2: Создать runbook**

```markdown
# docs/runbooks/finam-access-checklist.md

# Finam API Access Checklist

## Stage 0 — первоначальное подключение

- [ ] Счёт у ФИНАМ открыт
- [ ] Счёт имеет доступ к FORTS (срочный рынок)
- [ ] `account_id` известен (из личного кабинета)
- [ ] Secret token выпущен на https://api.finam.ru/tokens
- [ ] Сохранён в `~/.shectory_trade.env` (НЕ в репозиторий)
- [ ] Запущен: `poetry run pytest tests/auth/test_auth_integration.py -v -m integration`
- [ ] Тест PASS

## Переменные окружения

| Переменная | Описание |
|---|---|
| FINAM_SECRET_TOKEN | API secret token |
| FINAM_ACCOUNT_ID | Номер торгового счёта |
| FINAM_API_BASE_URL | Base URL (default: https://api.trade.finam.ru) |
| FINAM_TOKEN_REFRESH_BEFORE_SECS | Обновлять JWT за N секунд до истечения (default: 60) |

## Ограничения

- **Rate limit:** до 200 вызовов/мин на метод
- **Техобслуживание:** 05:00–06:15 МСК ежедневно — API недоступен
- **WebSocket:** разрывается раз в 24ч — авто-reconnect реализуется в M1

## Ротация токена

При компрометации:
1. Отозвать на https://api.finam.ru/tokens
2. Создать новый
3. Обновить в `~/.shectory_trade.env`
4. **Никогда** не коммитить реальный токен в git
```

- [ ] **Шаг 3: Запустить интеграционный тест (требует реального токена)**

```bash
# Загрузить реальные credentials
set -a && source ~/.shectory_trade.env && set +a

poetry run pytest tests/auth/test_auth_integration.py -v -m integration
```

Ожидаемый вывод при корректном токене: `2 passed`

При `401/403`: проверить значение `FINAM_SECRET_TOKEN`
При `404`: скорректировать `_TOKEN_PATH` в `trader/auth/client.py` по документации Finam

- [ ] **Шаг 4: Commit**

```bash
git add tests/auth/test_auth_integration.py docs/runbooks/finam-access-checklist.md
git commit -m "feat(M0): integration test + Finam access runbook"
```

---

### Task 6: CI (GitHub Actions)

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Шаг 1: Создать директорию и workflow**

```bash
mkdir -p .github/workflows
```

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install Poetry
        run: pip install poetry

      - name: Install dependencies
        run: poetry install

      - name: Lint
        run: poetry run ruff check trader/ tests/

      - name: Unit tests (no integration)
        run: poetry run pytest -m "not integration" -v
```

- [ ] **Шаг 2: Запустить локально для проверки**

```bash
poetry run pytest -m "not integration" -v
```

Ожидаемый вывод: все 8 тестов PASS

- [ ] **Шаг 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: GitHub Actions — lint + unit tests on push"
```

---

### Task 7: CLAUDE.md для проекта

**Files:**
- Modify: `CLAUDE.md` (создать, если не существует)

- [ ] **Шаг 1: Создать/обновить `CLAUDE.md`**

```markdown
# Shectory Trade & Lab — Project Instructions

## Overview
- **Shectory Trader**: execution platform for FORTS ММВБ via Finam Trade API (Python/FastAPI)
- **Shectory Lab**: strategy framework (future; connects to Trader via M8 Trader API)
- VDS: shectory-work (Ubuntu), deployed as systemd service

## Tech Stack
- Python 3.12, FastAPI, asyncio
- httpx[http2] — all HTTP calls (HTTP/2 required by Finam)
- pydantic-settings — config from env vars only
- structlog — structured logging
- pytest + pytest-asyncio — TDD, always test-first

## Commands
```bash
cd ~/workspaces/Shectory\ Trade\ \&\ Lab

poetry install                                      # install deps
poetry run pytest -m "not integration" -v           # unit tests (no credentials needed)
poetry run pytest -m integration -v                 # integration (needs FINAM_SECRET_TOKEN)
poetry run ruff check trader/ tests/                # lint
```

## Architecture Modules (from TZ)
| Module | Path | TZ Stage | Status |
|---|---|---|---|
| M0 Auth | trader/auth/ | Stage 2 | ✅ |
| M10 Config | trader/config.py | Stage 1 | ✅ |
| M4 Instrument Registry | trader/registry/ | Stage 3 | 🔲 |
| M1 Market Data | trader/md/ | Stage 4 | 🔲 |
| M2 TX Adapter | trader/tx/ | Stage 5 | 🔲 |
| M3 OMS Core | trader/oms/ | Stage 5 | 🔲 |
| M5 Positions | trader/positions/ | Stage 6 | 🔲 |
| M7 Audit Log | trader/audit/ | Stage 6 | 🔲 |
| M8 Trader API | trader/api/ | Stage 8 | 🔲 |
| M6 Risk Gate | trader/risk/ | Stage 9 | 🔲 |

## Key Constraints
- Secrets NEVER in git — use env vars or `~/.shectory_trade.env`
- All Finam calls: HTTP/2 via httpx
- Rate limit: 200 req/min per method
- Maintenance window: 05:00–06:15 МСК daily
- WebSocket: reconnect every 24h (auto-reconnect required in M1)
- TZ: docs/TZ_Shectory_Trader_v2_FINAM.md
```

- [ ] **Шаг 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md with project structure and commands"
```

---

## Self-Review

**Spec coverage (TZ_Shectory_Trader_v2_FINAM.md):**
- [x] Этап 0 (доступ) — ручные шаги + runbook docs/runbooks/finam-access-checklist.md
- [x] Этап 1 (репозиторий, окружения, секреты) — Task 1 (pyproject, .env.example, .gitignore уже есть)
- [x] Этап 2 (M0 Auth) — Tasks 3, 4, 5 (models + client + integration test)
- [x] M10 Config — Task 2
- [x] HTTP/2 requirement — httpx(http2=True) в AsyncAuthClient
- [x] Секреты не в git — .gitignore уже покрывает .env, .env.example без реальных значений
- [x] CI (lint + unit tests) — Task 6
- [x] Логирование — structlog в client.py

**Placeholder scan:**
- `# TODO: Verify exact path` в client.py — намеренный флаг, не убирать до проверки документации Finam

**Type consistency:**
- `TokenResponse` — определён в models.py Task 3, используется в client.py Task 4 и conftest.py Task 4 ✓
- `AsyncAuthClient` — определён в client.py Task 4, используется в интеграционном тесте Task 5 ✓

---

## Следующий план

После завершения всех задач и прохождения интеграционного теста:
→ **Plan 2: M4 Instrument Registry (Stage 3 ТЗ)** — загрузка справочника инструментов, поиск `GAZR-6.26` → `symbol@mic`, фиксация в конфигурации MVP.
