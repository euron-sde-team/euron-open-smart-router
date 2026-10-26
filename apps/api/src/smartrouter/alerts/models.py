"""SQLAlchemy models for the alerts domain."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from smartrouter.core.db import Base, TimestampMixin


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False, default="warning")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
