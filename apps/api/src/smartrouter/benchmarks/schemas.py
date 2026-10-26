from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from smartrouter.core.schemas import ApiModel, Usd


class RunIn(ApiModel):
    models: list[str] | None = Field(None, description="Model aliases; null = every enabled model")
    policies: list[str] | None = Field(
        None, description="Policies; null = every enabled policy, [] = none"
    )


class RunOut(ApiModel):
    id: uuid.UUID
    status: str
    suite_version: str
    models: list[str]
    policies: list[str]
    summary: dict[str, Any]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ResultOut(ApiModel):
    target_type: str
    target: str
    task: str
    band: str
    cases: int
    passed: int
    score: float
    cost_usd: Usd = Field(validation_alias="cost_nusd")
    latency_ms_avg: float
    model_mix: dict[str, Any]


class RunDetail(RunOut):
    results: list[ResultOut] = []


class SuiteCase(ApiModel):
    id: str
    task: str
    difficulty: int
    grader: str
    prompt: str


class CasesDownload(ApiModel):
    url: str = Field(description="Presigned S3 link, or an API path that streams the file")
    filename: str
    rows: int
    bytes: int
