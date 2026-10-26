from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, HttpUrl

from smartrouter.core.schemas import ApiModel

EndpointKind = Literal["openai", "anthropic", "tokenshield"]
Tier = Literal["fast", "low-cost", "advanced", "local"]


class ProviderIn(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    kind: EndpointKind
    base_url: HttpUrl
    api_key: str | None = Field(None, max_length=4096, description="Write-only; sealed at rest")
    timeout_s: float | None = Field(None, gt=0, le=600)
    simulated: bool = Field(
        False, description="The mock LLM: may receive benchmark oracle annotations"
    )
    enabled: bool = True


class ProviderUpdate(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    base_url: HttpUrl | None = None
    api_key: str | None = Field(None, max_length=4096, description="Empty string clears the key")
    timeout_s: float | None = Field(None, gt=0, le=600)
    enabled: bool | None = None


class ProviderOut(ApiModel):
    id: uuid.UUID
    name: str
    kind: str
    base_url: str
    has_key: bool
    key_hint: str | None
    timeout_s: float | None
    simulated: bool
    enabled: bool
    created_at: datetime
    model_count: int = 0


class ProviderTest(ApiModel):
    ok: bool
    latency_ms: int
    message: str


class ModelIn(ApiModel):
    alias: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._/-]+$")
    provider_id: uuid.UUID
    upstream_model: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=500)
    tier: Tier = "advanced"
    tokenizer: str = Field("o200k_base", max_length=32)
    context_window: int = Field(128000, ge=256, le=10_000_000)
    max_output_tokens: int = Field(16384, ge=1, le=1_000_000)
    input_per_m: Decimal = Field(Decimal(0), ge=0, le=100000, description="USD per 1M input tokens")
    output_per_m: Decimal = Field(
        Decimal(0), ge=0, le=100000, description="USD per 1M output tokens"
    )
    supports_tools: bool = True
    supports_vision: bool = False
    supports_json: bool = True
    latency_prior_ms: int = Field(1000, ge=1, le=600_000)
    enabled: bool = True


class ModelUpdate(ApiModel):
    upstream_model: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=500)
    tier: Tier | None = None
    context_window: int | None = Field(None, ge=256, le=10_000_000)
    max_output_tokens: int | None = Field(None, ge=1, le=1_000_000)
    input_per_m: Decimal | None = Field(None, ge=0, le=100000)
    output_per_m: Decimal | None = Field(None, ge=0, le=100000)
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_json: bool | None = None
    latency_prior_ms: int | None = Field(None, ge=1, le=600_000)
    enabled: bool | None = None


class ModelOut(ApiModel):
    id: uuid.UUID
    alias: str
    provider_id: uuid.UUID
    provider_name: str
    provider_kind: str
    upstream_model: str
    description: str
    tier: str
    context_window: int
    max_output_tokens: int
    input_per_m: Decimal
    output_per_m: Decimal
    supports_tools: bool
    supports_vision: bool
    supports_json: bool
    latency_prior_ms: int
    enabled: bool
    created_at: datetime
