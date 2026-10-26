"""Benchmark runs and their per-target, per-task results."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from smartrouter.core.db import Base, TimestampMixin, new_id


class BenchmarkRun(TimestampMixin, Base):
    __tablename__ = "benchmark_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued")
    suite_version: Mapped[str] = mapped_column(String(32), nullable=False)
    models: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    policies: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class BenchmarkResult(Base):
    __tablename__ = "benchmark_results"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_type: Mapped[str] = mapped_column(String(8), nullable=False)  # model | policy
    target: Mapped[str] = mapped_column(String(120), nullable=False)
    task: Mapped[str] = mapped_column(String(24), nullable=False)
    band: Mapped[str] = mapped_column(String(8), nullable=False)
    cases: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    cost_nusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    latency_ms_avg: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    # For policy targets: which models were picked, {alias: count}
    model_mix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
