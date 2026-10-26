"""OpenAI-compatible gateway endpoints. Point any OpenAI SDK at ``<host>/v1`` and send
``model="auto"`` (or ``auto:<policy>``) to let SmartRouter choose."""

from __future__ import annotations

from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import Field
from redis.exceptions import RedisError
from sqlalchemy import select

from smartrouter.core.deps import ContainerDep, SessionDep
from smartrouter.core.errors import AppError, NotFoundError, RateLimitedError
from smartrouter.core.schemas import ApiModel, Message
from smartrouter.decisions.models import DecisionFeedback, RoutingDecision
from smartrouter.decisions.recorder import REQUEST_KEY
from smartrouter.gateway.catalog import Catalog
from smartrouter.gateway.dependencies import authenticate_key, get_catalog
from smartrouter.gateway.service import GatewayService, RequestContext
from smartrouter.limits.limiter import Limiter

router = APIRouter(prefix="/v1", tags=["gateway"])
MAX_BODY_BYTES = 8 * 1024 * 1024
FEEDBACK_RPM = 600  # per key, when the key sets no rpm limit
CtxDep = Annotated[RequestContext, Depends(authenticate_key)]
CatalogDep = Annotated[Catalog, Depends(get_catalog)]


async def _json(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise AppError("Request body too large", code="body_too_large")
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError:
        raise AppError("Request body must be JSON", code="invalid_json") from None
    if not isinstance(data, dict):
        raise AppError("Request body must be a JSON object", code="invalid_json")
    if not isinstance(data.get("messages"), list) or not data["messages"]:
        raise AppError("'messages' must be a non-empty list", code="invalid_request")
    return data


@router.post("/chat/completions", summary="Create a chat completion; model='auto' routes it")
async def chat_completions(
    request: Request, ctx: CtxDep, container: ContainerDep, catalog: CatalogDep
) -> Response:
    return await GatewayService(container, catalog).chat(ctx, await _json(request))


@router.get("/models", summary="Routes and models available to this key")
async def list_models(ctx: CtxDep, catalog: CatalogDep) -> dict[str, Any]:
    routes = [
        {
            "id": "auto",
            "object": "model",
            "created": 0,
            "owned_by": "smartrouter",
            "smartrouter": {
                "kind": "route",
                "policy": (ctx.project.default_policy if ctx.project else None)
                or catalog.default_policy,
            },
        }
    ] + [
        {
            "id": f"auto:{name}",
            "object": "model",
            "created": 0,
            "owned_by": "smartrouter",
            "smartrouter": {"kind": "route", "policy": name, "strategy": p.strategy},
        }
        for name, p in sorted(catalog.policies.items())
    ]
    models = [
        {
            "id": m.view.alias,
            "object": "model",
            "created": 0,
            "owned_by": m.view.endpoint,
            "smartrouter": {
                "kind": "model",
                "tier": m.view.tier,
                "context_window": m.view.context_window,
            },
        }
        for m in sorted(catalog.models.values(), key=lambda m: m.view.alias)
    ]
    return {"object": "list", "data": routes + models}


class FeedbackIn(ApiModel):
    request_id: str = Field(min_length=4, max_length=64)
    score: int = Field(description="1 (good answer) or -1 (bad answer)")
    comment: str | None = Field(None, max_length=2000)


@router.post("/feedback", response_model=Message, summary="Rate the answer to a routed request")
async def feedback(
    body: FeedbackIn, ctx: CtxDep, session: SessionDep, container: ContainerDep
) -> Message:
    if body.score not in (1, -1):
        raise AppError("score must be 1 or -1", code="invalid_score")
    if ctx.key:
        try:
            limit = await Limiter(container.redis).hit(
                f"feedback:{ctx.key.id}", ctx.key.rpm_limit or FEEDBACK_RPM
            )
        except RedisError:
            limit = None  # fail open, like the gateway's own limits
        if limit is not None and not limit.allowed:
            raise RateLimitedError(
                "Too much feedback from this key; slow down",
                code="rpm_limit",
                headers={"retry-after": str(limit.retry_after_s)},
            )
    row = await session.scalar(
        select(RoutingDecision.project_id).where(RoutingDecision.request_id == body.request_id)
    )
    if row is not None:
        owner: str | None = str(row)
    else:
        # The worker may not have written the row yet: the recorder leaves a marker.
        try:
            marker = await container.redis.get(REQUEST_KEY.format(body.request_id))
        except RedisError:
            marker = None
        owner = (
            None if marker is None else (marker.decode() if isinstance(marker, bytes) else marker)
        )
    if owner is None:
        raise NotFoundError("No routed request with that id. Use x-smartrouter-request-id.")
    if ctx.project and owner and owner != str(ctx.project.id):
        raise AppError("That request belongs to another project", code="not_your_request")
    session.add(
        DecisionFeedback(request_id=body.request_id, score=body.score, comment=body.comment)
    )
    await session.commit()
    return Message(message="Thanks: feedback recorded")
