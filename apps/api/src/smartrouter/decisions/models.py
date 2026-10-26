"""The routing decision log (append-only), feedback and hourly rollups."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from smartrouter.core.db import Base


class RoutingDecision(Base):
    """One row per gateway request. A database trigger refuses UPDATE and DELETE."""

    __tablename__ = "routing_decisions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(String(12), nullable=False, default="api")
    requested_model: Mapped[str] = mapped_column(String(120), nullable=False)
    policy_name: Mapped[str | None] = mapped_column(String(64))
    strategy: Mapped[str] = mapped_column(String(16), nullable=False)
    task: Mapped[str] = mapped_column(String(24), nullable=False)
    complexity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    signals: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    input_tokens_est: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_model: Mapped[str | None] = mapped_column(String(120))
    final_model: Mapped[str | None] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    fallback_chain: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    floor_relaxed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    estimated_cost_nusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    baseline_model: Mapped[str | None] = mapped_column(String(120))
    baseline_cost_nusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    actual_cost_nusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_source: Mapped[str] = mapped_column(String(12), nullable=False, default="none")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(40))
    http_status: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    upstream_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    routing_us: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stream: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    upstream_request_id: Mapped[str | None] = mapped_column(String(120))


class DecisionFeedback(Base):
    """Quality feedback from the application (append-only)."""

    __tablename__ = "decision_feedback"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # -1 or 1
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DecisionRollupHourly(Base):
    __tablename__ = "decision_rollups_hourly"
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    policy_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    task: Mapped[str] = mapped_column(String(24), primary_key=True)
    final_model: Mapped[str] = mapped_column(String(120), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), primary_key=True)
    requests: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    fallbacks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_nusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    baseline_cost_nusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    latency_ms_sum: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
