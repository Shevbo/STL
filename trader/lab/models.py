from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class StlLink(BaseModel):
    id: str
    user_email: str
    broker: str = "finam"
    exchange: str = "FORTS"
    account_id: str
    instruments: list[str] = Field(default_factory=list)
    operations: str = "RW"
    enabled: bool = True
    created_at: datetime | None = None


class Robot(BaseModel):
    id: str
    user_email: str
    stl_link_id: str
    name: str
    script_code: str
    params_json: dict[str, Any] = Field(default_factory=dict)
    state_json: dict[str, Any] = Field(default_factory=dict)
    schedule: str = "*/5 * * * *"
    deployed: bool = False
    deployed_at: datetime | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BacktestRun(BaseModel):
    id: str
    robot_id: str
    params_grid: dict[str, Any]
    date_from: str
    date_to: str
    status: str = "pending"
    error_msg: str | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None


class BacktestResult(BaseModel):
    id: str
    run_id: str
    params: dict[str, Any]
    trades: list[dict] = Field(default_factory=list)
    equity_curve: list[dict] = Field(default_factory=list)
    sharpe: float | None = None
    max_drawdown: float | None = None
    win_rate: float | None = None
    total_return: float | None = None
    total_trades: int | None = None


class LiveTrade(BaseModel):
    id: str
    robot_id: str
    symbol: str
    side: str
    qty: int
    price: Decimal
    order_id: str | None = None
    status: str
    timestamp: datetime


class LiveMetric(BaseModel):
    id: str
    robot_id: str
    equity: Decimal
    pnl: Decimal
    positions: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime
