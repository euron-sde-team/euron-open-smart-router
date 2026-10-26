"""SQLAlchemy models for the projects domain."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from smartrouter.core.db import Base, TimestampMixin, new_id


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Policy used for model="auto" from this project's keys; null = the gateway default.
    default_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("routing_policies.id", ondelete="SET NULL")
    )
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
