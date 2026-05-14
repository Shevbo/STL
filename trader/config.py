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
