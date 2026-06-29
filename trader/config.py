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
    # Shared secret for the external optimization agent (Windows host) to claim
    # jobs and post results. If empty, the agent endpoints are disabled.
    opt_agent_token: SecretStr = SecretStr("")
    # Bearer token the QUIK agent (Windows, Go) presents on the gRPC Session
    # stream (sprint02). Provisioned via keymaster, never hardcoded. If empty,
    # the link falls back to the portal session secret (shectory_auth_bridge_secret).
    quik_agent_token: SecretStr = SecretStr("")
    # gRPC listen address for the QUIK agent link. The agent dials OUT to this.
    quik_agent_grpc_listen: str = "0.0.0.0:50061"
    # Enable the QUIK agent gRPC server at startup. Off by default so a plain
    # deploy does not open a new port until the operator opts in.
    quik_agent_enabled: bool = False
    # Link freshness lamp threshold: no heartbeat/message within this many seconds
    # turns the agent link lamp from green to red (PiranhaAI agent_link_fresh_sec).
    quik_agent_link_fresh_sec: int = 15
    # Exchange data-source interface selector persisted default: "finam" (current
    # Finam Trade API) or "quik" (QUIK agent). DATA SOURCE + status only; no routing.
    exchange_interface: str = "finam"
    # QUIK agent alert forwarder (sprint02). Telegram Bot API credentials, by ENV
    # NAME only — never a hardcoded value. If either is empty the forwarder no-ops
    # (logs a warning) instead of sending.
    quik_alert_tg_token: SecretStr = SecretStr("")
    quik_alert_tg_chat_id: str = ""
    # De-dupe cooldown: suppress a repeated (agent, code, severity) alert within
    # this many seconds. Recovery alerts always pass.
    quik_alert_cooldown_sec: int = 60
