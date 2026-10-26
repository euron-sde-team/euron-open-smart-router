from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from smartrouter.core.schemas import ApiModel, Usd

Range = Literal["1h", "24h", "7d", "30d", "90d"]
Dimension = Literal["model", "task", "policy", "project", "status"]


class DecisionOut(ApiModel):
    id: int
    request_id: str
    created_at: datetime
    project_id: uuid.UUID | None
    project_name: str | None = None
    source: str
    requested_model: str
    policy_name: str | None
    strategy: str
    task: str
    complexity: int
    selected_model: str | None
    final_model: str | None
    attempts: int
    floor_relaxed: bool
    estimated_cost_usd: Usd = Field(validation_alias="estimated_cost_nusd")
    baseline_model: str | None
    baseline_cost_usd: Usd = Field(validation_alias="baseline_cost_nusd")
    actual_cost_usd: Usd = Field(validation_alias="actual_cost_nusd")
    cost_source: str
    input_tokens: int
    output_tokens: int
    status: str
    error_code: str | None
    http_status: int
    latency_ms: int
    upstream_ms: int
    routing_us: int
    stream: bool
    feedback: int | None = None


class DecisionDetail(DecisionOut):
    signals: list[str]
    input_tokens_est: int
    reason: str
    fallback_chain: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    upstream_request_id: str | None


class Totals(ApiModel):
    requests: int = 0
    succeeded: int = 0
    failed: int = 0
    fallbacks: int = 0
    cost_usd: float = 0
    baseline_cost_usd: float = 0
    savings_usd: float = 0
    savings_pct: float = 0
    avg_latency_ms: float = 0
    fallback_rate: float = 0
    input_tokens: int = 0
    output_tokens: int = 0


class Overview(ApiModel):
    range: str
    since: datetime
    totals: Totals
    previous: Totals
    routing_overhead_us_p50: float | None = None
    feedback_positive_rate: float | None = None


class SeriesPoint(ApiModel):
    t: datetime
    requests: int
    cost_usd: float
    baseline_cost_usd: float
    fallbacks: int
    failed: int


class BreakdownRow(ApiModel):
    key: str
    label: str
    requests: int
    cost_usd: float
    baseline_cost_usd: float
    savings_usd: float
    share: float
    avg_latency_ms: float
