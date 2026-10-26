"""Records routing decisions through a Redis Stream drained by workers.

The request path appends with ``XADD``, pipelined with a short-lived request id marker (one
round trip); workers insert batches into Postgres through a consumer group and ack only after
the commit. If Redis rejects the append, the decision is written straight to Postgres.
``request_id`` is unique, so a replayed batch never duplicates a row.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import orjson
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from smartrouter.decisions.models import RoutingDecision

log = logging.getLogger("smartrouter.decisions")
STREAM_KEY = "sr:decisions:stream"
GROUP = "recorders"
STREAM_MAXLEN = 1_000_000
# request id -> project id, for the minutes before the worker writes the decision row
# (feedback can arrive first). A day is far longer than the drain ever lags.
REQUEST_KEY = "sr:req:{}"
REQUEST_TTL_S = 86_400


@dataclass(slots=True)
class DecisionRecord:
    request_id: str
    requested_model: str
    strategy: str
    task: str
    complexity: int
    status: str
    project_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None
    source: str = "api"
    policy_name: str | None = None
    signals: list[str] = field(default_factory=list)
    input_tokens_est: int = 0
    selected_model: str | None = None
    final_model: str | None = None
    reason: str = ""
    attempts: int = 0
    fallback_chain: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    floor_relaxed: bool = False
    estimated_cost_nusd: int = 0
    baseline_model: str | None = None
    baseline_cost_nusd: int = 0
    actual_cost_nusd: int = 0
    cost_source: str = "none"
    input_tokens: int = 0
    output_tokens: int = 0
    error_code: str | None = None
    http_status: int = 200
    latency_ms: int = 0
    upstream_ms: int = 0
    routing_us: int = 0
    stream: bool = False
    upstream_request_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


_UUIDS = ("project_id", "api_key_id")


def encode(rec: DecisionRecord) -> bytes:
    return orjson.dumps(asdict(rec), default=str)


def decode(raw: bytes) -> DecisionRecord:
    data: dict[str, Any] = orjson.loads(raw)
    for k in _UUIDS:
        if data.get(k):
            data[k] = uuid.UUID(data[k])
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    return DecisionRecord(**data)


class DecisionRecorder:
    def __init__(self, sessionmaker: async_sessionmaker, redis: Redis | None = None) -> None:  # type: ignore[type-arg]
        self._sessionmaker = sessionmaker
        self._redis = redis

    async def record(self, rec: DecisionRecord) -> None:
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline(transaction=False)
                pipe.xadd(STREAM_KEY, {"r": encode(rec)}, maxlen=STREAM_MAXLEN, approximate=True)
                pipe.set(
                    REQUEST_KEY.format(rec.request_id),
                    str(rec.project_id or ""),
                    ex=REQUEST_TTL_S,
                )
                await pipe.execute()
                return
            except RedisError:
                log.warning(
                    "decision stream unavailable; writing directly", extra={"rid": rec.request_id}
                )
        await self.write([rec])

    async def write(self, records: Sequence[DecisionRecord]) -> None:
        if not records:
            return
        rows = [asdict(r) for r in records]
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                insert(RoutingDecision)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["request_id"])
            )
