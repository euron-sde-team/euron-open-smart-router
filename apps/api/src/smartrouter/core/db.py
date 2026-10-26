"""Database engine, session factory and the declarative base shared by every domain."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[str]: JSONB}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Database:
    """Owns the engine and session factory for one application instance."""

    def __init__(self, url: str, *, pool_size: int = 10, max_overflow: int = 20) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()


def import_all_models() -> None:
    """Import every domain's models so the ORM mapping is complete (Alembic, Container)."""
    from smartrouter.alerts import models as _alerts  # noqa: F401
    from smartrouter.audit import models as _audit  # noqa: F401
    from smartrouter.benchmarks import models as _benchmarks  # noqa: F401
    from smartrouter.decisions import models as _decisions  # noqa: F401
    from smartrouter.keys import models as _keys  # noqa: F401
    from smartrouter.projects import models as _projects  # noqa: F401
    from smartrouter.providers import models as _providers  # noqa: F401
    from smartrouter.routing import models as _routing  # noqa: F401
    from smartrouter.teams import models as _teams  # noqa: F401
    from smartrouter.users import models as _users  # noqa: F401
