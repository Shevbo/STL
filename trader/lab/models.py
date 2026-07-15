from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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
