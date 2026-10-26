from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from smartrouter.core.schemas import ApiModel
from smartrouter.routing.analyzer import TASKS

Strategy = Literal["cost", "latency", "quality", "balanced"]
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"


def _check_floors(v: dict[str, float]) -> dict[str, float]:
    for task, floor in v.items():
        if task not in TASKS:
            raise ValueError(f"unknown task '{task}'; tasks are {', '.join(TASKS)}")
        if not 0 <= float(floor) <= 1:
            raise ValueError(f"floor for {task} must be between 0 and 1")
    return {k: float(x) for k, x in v.items()}


def _check_weights(v: dict[str, float] | None) -> dict[str, float] | None:
    if v is None:
        return None
    if set(v) - {"cost", "latency", "quality"}:
        raise ValueError("weights take cost, latency and quality")
    if any(float(x) < 0 for x in v.values()) or sum(float(x) for x in v.values()) <= 0:
        raise ValueError("weights must be non-negative and not all zero")
    return {k: float(x) for k, x in v.items()}


class PolicyIn(ApiModel):
    name: str = Field(
        pattern=NAME_PATTERN, description="Lowercase; clients send model='auto:<name>'"
    )
    description: str = Field("", max_length=500)
    strategy: Strategy = "balanced"
    weights: dict[str, float] | None = None
    quality_floor: float = Field(0.7, ge=0, le=1)
    task_floors: dict[str, float] = Field(default_factory=dict)
    hard_floor_boost: float = Field(0.1, ge=0, le=0.5)
    max_latency_ms: int | None = Field(None, ge=1, le=600_000)
    max_cost_usd: Decimal | None = Field(None, gt=0, le=1000)
    allowed_models: list[str] | None = None
    excluded_models: list[str] = Field(default_factory=list)
    allow_fallback: bool = True
    max_attempts: int = Field(3, ge=1, le=6)
    budget_downgrade: bool = False
    on_no_eligible: Literal["best_quality", "reject"] = "best_quality"
    baseline_model: str | None = None
    is_default: bool = False
    enabled: bool = True

    _floors = field_validator("task_floors")(classmethod(lambda cls, v: _check_floors(v)))
    _weights = field_validator("weights")(classmethod(lambda cls, v: _check_weights(v)))


class PolicyUpdate(ApiModel):
    description: str | None = Field(None, max_length=500)
    strategy: Strategy | None = None
    weights: dict[str, float] | None = None
    quality_floor: float | None = Field(None, ge=0, le=1)
    task_floors: dict[str, float] | None = None
    hard_floor_boost: float | None = Field(None, ge=0, le=0.5)
    max_latency_ms: int | None = Field(None, ge=1, le=600_000)
    max_cost_usd: Decimal | None = Field(None, gt=0, le=1000)
    allowed_models: list[str] | None = None
    excluded_models: list[str] | None = None
    allow_fallback: bool | None = None
    max_attempts: int | None = Field(None, ge=1, le=6)
    budget_downgrade: bool | None = None
    on_no_eligible: Literal["best_quality", "reject"] | None = None
    baseline_model: str | None = None
    is_default: bool | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _validate(self) -> PolicyUpdate:
        if self.task_floors is not None:
            self.task_floors = _check_floors(self.task_floors)
        self.weights = _check_weights(self.weights)
        return self


class PolicyOut(ApiModel):
    id: uuid.UUID
    name: str
    description: str
    strategy: str
    weights: dict[str, float] | None
    effective_weights: dict[str, float] = Field(default_factory=dict)
    quality_floor: float
    task_floors: dict[str, float]
    hard_floor_boost: float
    max_latency_ms: int | None
    max_cost_usd: Decimal | None
    allowed_models: list[str] | None
    excluded_models: list[str]
    allow_fallback: bool
    max_attempts: int
    budget_downgrade: bool
    on_no_eligible: str
    baseline_model: str | None
    is_default: bool
    enabled: bool
    created_at: datetime
    requests_24h: int = 0


class SimulateIn(ApiModel):
    model: str = Field("auto", description="auto, auto:<policy> or a model alias")
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    project_id: uuid.UUID | None = None
    max_tokens: int | None = Field(None, ge=1, le=1_000_000)
    tools: list[dict[str, Any]] | None = None
    response_format: dict[str, Any] | None = None
    task: str | None = Field(None, description="Override the detected task")
    complexity: int | None = Field(None, ge=1, le=5)


class AnalysisOut(ApiModel):
    task: str
    complexity: int
    band: str
    signals: list[str]
    input_tokens: int
    output_tokens: int
    needs_tools: bool
    needs_vision: bool
    needs_json: bool


class SimulateOut(ApiModel):
    policy: str
    strategy: str
    passthrough: bool
    floor: float
    floor_relaxed: bool
    analysis: AnalysisOut
    selected: str | None
    baseline_model: str | None
    baseline_cost_usd: float
    estimated_cost_usd: float
    estimated_savings_pct: float | None
    candidates: list[dict[str, Any]]
    routing_us: int


class QualityCell(ApiModel):
    model_alias: str
    task: str
    band: str
    score: float
    samples: int
    source: str
    updated_at: datetime


class QualityOverride(ApiModel):
    model_alias: str
    task: str
    band: Literal["low", "high"]
    score: float = Field(ge=0, le=1)
