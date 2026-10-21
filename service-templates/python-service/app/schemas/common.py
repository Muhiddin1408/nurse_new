from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class WhoAmI(BaseModel):
    user_id: UUID
    roles: list[str]
