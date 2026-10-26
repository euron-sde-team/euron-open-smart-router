"""Alerts for operators: today, a model whose circuit opened.

Each alert carries a dedupe key (model and hour), so a model failing a thousand times in an
hour produces one alert, not a thousand rows.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from smartrouter.alerts.models import Alert


class AlertService:
    def __init__(self, sessionmaker: async_sessionmaker) -> None:  # type: ignore[type-arg]
        self._sessionmaker = sessionmaker

    async def raise_alert(
        self,
        kind: str,
        message: str,
        *,
        dedupe_key: str,
        severity: str = "warning",
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                insert(Alert)
                .values(
                    kind=kind,
                    message=message,
                    severity=severity,
                    dedupe_key=dedupe_key,
                )
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
            )


log = logging.getLogger("smartrouter.health")


async def circuit_opened(sessionmaker: async_sessionmaker, alias: str, source: str) -> None:  # type: ignore[type-arg]
    """A model's circuit is open: one structured log line per failure (CloudWatch counts
    these as CircuitOpened) and at most one alert per model per hour."""
    log.warning("circuit open", extra={"event": "circuit_opened", "model": alias, "source": source})
    hour = datetime.now(UTC).strftime("%Y%m%d%H")
    await AlertService(sessionmaker).raise_alert(
        "health",
        f"Circuit opened for {alias}: repeated failures; traffic is routed to other models",
        dedupe_key=f"circuit:{alias}:{hour}",
        severity="critical",
    )
