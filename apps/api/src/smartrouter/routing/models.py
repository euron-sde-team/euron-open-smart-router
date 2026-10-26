"""Routing policies and the quality table the router ranks by."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from smartrouter.core.db import Base, TimestampMixin, new_id


class RoutingPolicy(TimestampMixin, Base):
    __tablename__ = "routing_policies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    # Clients select a policy with model="auto:<name>".
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    strategy: Mapped[str] = mapped_column(String(16), nullable=False, default="balanced")
    # Custom weights override the strategy's: {"cost": w, "latency": w, "quality": w}
    weights: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    quality_floor: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    task_floors: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Added to the floor for hard requests (complexity 4 and 5).
    hard_floor_boost: Mapped[float] = mapped_column(Float, nullable=False, default=0.1)
    max_latency_ms: Mapped[int | None] = mapped_column(Integer)
    max_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    allowed_models: Mapped[list[str] | None] = mapped_column(JSONB)
    excluded_models: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    allow_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # On a TokenShield 402, retry on a cheaper eligible model.
    budget_downgrade: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # When no model clears the floor: reject, or take the best-quality capable model.
    on_no_eligible: Mapped[str] = mapped_column(String(16), nullable=False, default="best_quality")
    # Savings are measured against this model (default: the most expensive capable model).
    baseline_model: Mapped[str | None] = mapped_column(String(120))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class QualityScore(Base):
    """Latest benchmark score per model, task and difficulty band (low: 1 to 3, high: 4 and 5)."""

    __tablename__ = "quality_scores"
    __table_args__ = (UniqueConstraint("model_alias", "task", "band"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_alias: Mapped[str] = mapped_column(String(120), nullable=False)
    task: Mapped[str] = mapped_column(String(24), nullable=False)
    band: Mapped[str] = mapped_column(String(8), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="benchmark")
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
