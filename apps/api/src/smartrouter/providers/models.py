"""Endpoints (upstream APIs) and the models they serve."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from smartrouter.core.db import Base, TimestampMixin, new_id


class Provider(TimestampMixin, Base):
    """An upstream endpoint. Shown as "Endpoints" in the dashboard."""

    __tablename__ = "providers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    # openai (any OpenAI-compatible API) | anthropic | tokenshield (a TokenShield gateway)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    sealed_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_hint: Mapped[str | None] = mapped_column(String(16))
    timeout_s: Mapped[float | None] = mapped_column(Numeric(6, 1))
    # A simulated endpoint (the mock LLM) may receive benchmark oracle annotations; a real
    # provider never does.
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Model(TimestampMixin, Base):
    __tablename__ = "models"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    alias: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    upstream_model: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # fast | low-cost | advanced | local: a label for people and the prior quality guess
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="advanced")
    tokenizer: Mapped[str] = mapped_column(String(32), nullable=False, default="o200k_base")
    context_window: Mapped[int] = mapped_column(Integer, nullable=False, default=128000)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=16384)
    input_per_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0)
    output_per_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0)
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supports_vision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_json: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Used for latency ranking until live measurements exist.
    latency_prior_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
