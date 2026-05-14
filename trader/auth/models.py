from datetime import datetime, timezone
from pydantic import BaseModel


class TokenResponse(BaseModel):
    access_token: str
    expires_at: datetime

    def is_expired(self, buffer_secs: int = 60) -> bool:
        now = datetime.now(timezone.utc)
        return (self.expires_at - now).total_seconds() < buffer_secs
