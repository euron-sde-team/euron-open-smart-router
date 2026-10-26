"""Gateway authentication and request context."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from redis.exceptions import RedisError
from sqlalchemy import update

from smartrouter.core.container import Container
from smartrouter.core.deps import get_container
from smartrouter.core.errors import AuthenticationError, PermissionDeniedError
from smartrouter.core.security import constant_time_equals, hash_key_secret, parse_api_key
from smartrouter.gateway.catalog import Catalog, KeyInfo, load_catalog, load_key
from smartrouter.gateway.service import RequestContext, new_request_id
from smartrouter.keys.models import ApiKey

_tasks: set[asyncio.Task[None]] = set()
_MAX = 120


async def get_catalog(container: Annotated[Container, Depends(get_container)]) -> Catalog:
    async def load() -> Catalog:
        async with container.db.sessionmaker() as session:
            return await load_catalog(session, container.sealer)

    return await container.config_cache.get("catalog", load)


async def _touch(container: Container, key_id: uuid.UUID) -> None:
    try:
        if not await container.redis.set(f"sr:keyused:{key_id}", b"1", nx=True, ex=60):
            return
    except RedisError:
        return
    async with container.db.sessionmaker() as session, session.begin():
        await session.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=datetime.now(UTC))
        )


def _header(request: Request, name: str) -> str | None:
    value = (request.headers.get(name) or "").strip()
    return value[:_MAX] or None


async def authenticate_key(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    catalog: Annotated[Catalog, Depends(get_catalog)],
) -> RequestContext:
    raw = request.headers.get("authorization", "")
    token = (
        raw[7:].strip()
        if raw.lower().startswith("bearer ")
        else request.headers.get("x-api-key", "")
    )
    parsed = parse_api_key(token) if token else None
    if parsed is None:
        raise AuthenticationError(
            "Missing or malformed API key. Send 'Authorization: Bearer tsr_...'",
            code="invalid_api_key",
        )
    prefix, secret = parsed

    async def load() -> KeyInfo | None:
        async with container.db.sessionmaker() as session:
            return await load_key(session, prefix)

    key = await container.config_cache.get(f"key:{prefix}", load)
    if key is None or not constant_time_equals(
        key.secret_hash, hash_key_secret(secret, container.settings.api_key_pepper)
    ):
        raise AuthenticationError("Invalid API key", code="invalid_api_key")
    if key.revoked:
        raise AuthenticationError("This API key has been revoked", code="api_key_revoked")
    if key.expires_at and key.expires_at <= datetime.now(UTC):
        raise AuthenticationError("This API key has expired", code="api_key_expired")
    project = catalog.projects.get(key.project_id)
    if project is None:
        raise PermissionDeniedError(
            "The project for this key is archived or missing", code="project_unavailable"
        )
    task = asyncio.get_running_loop().create_task(_touch(container, key.id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    complexity = _header(request, "x-smartrouter-complexity")
    return RequestContext(
        request_id=new_request_id(),
        project=project,
        key=key,
        policy_header=_header(request, "x-smartrouter-policy"),
        task_override=_header(request, "x-smartrouter-task"),
        complexity_override=int(complexity) if complexity and complexity.isdigit() else None,
    )
