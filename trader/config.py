import os
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_HOME_ENV = Path(os.path.expanduser("~/.shectory_trade.env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[str(_HOME_ENV), ".env"],
        env_file_encoding="utf-8",
        extra="ignore",
    )

    finam_api_base_url: str = "https://api.finam.ru"
    finam_secret_token: SecretStr
    finam_account_id: str = ""
    finam_token_refresh_before_secs: int = 60
    finam_mvp_symbol: str = ""
    shectory_portal_url: str = "https://shectory.ru"
    shectory_auth_bridge_secret: str = ""
    shectory_local_user_email: str = ""
    shectory_local_user_password_sha256: str = ""
    lab_db_url: str = ""
