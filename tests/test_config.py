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
