from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from smartrouter.core.schemas import ApiModel


class ProjectIn(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=2000)
    team_id: uuid.UUID | None = None
    default_policy: str | None = Field(
        None, description="Routing policy for model='auto'; null = gateway default"
    )


class ProjectUpdate(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    team_id: uuid.UUID | None = None
    default_policy: str | None = None
    archived: bool | None = None


class ProjectOut(ApiModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str
    team_id: uuid.UUID | None
    team_name: str | None = None
    default_policy: str | None = None
    archived: bool
    created_at: datetime
    key_count: int = 0
