from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from smartrouter.auth.dependencies import Admin, Viewer
from smartrouter.core.deps import ContainerDep, SessionDep
from smartrouter.gateway.catalog import Catalog
from smartrouter.gateway.dependencies import get_catalog
from smartrouter.gateway.service import GatewayService
from smartrouter.routing import service
from smartrouter.routing.schemas import (
    PolicyIn,
    PolicyOut,
    PolicyUpdate,
    QualityCell,
    QualityOverride,
    SimulateIn,
    SimulateOut,
)

router = APIRouter(prefix="/api/v1", tags=["routing"])
CatalogDep = Annotated[Catalog, Depends(get_catalog)]


@router.get("/policies", response_model=list[PolicyOut], summary="List routing policies")
async def list_policies(_: Viewer, session: SessionDep) -> list[PolicyOut]:
    return await service.list_policies(session)


@router.post(
    "/policies", response_model=PolicyOut, status_code=201, summary="Create a routing policy"
)
async def create_policy(
    body: PolicyIn, actor: Admin, session: SessionDep, container: ContainerDep
) -> PolicyOut:
    out = await service.create_policy(session, body, actor)
    await container.config_cache.invalidate()
    return out


@router.patch("/policies/{policy_id}", response_model=PolicyOut, summary="Update a routing policy")
async def update_policy(
    policy_id: uuid.UUID,
    body: PolicyUpdate,
    actor: Admin,
    session: SessionDep,
    container: ContainerDep,
) -> PolicyOut:
    out = await service.update_policy(session, policy_id, body, actor)
    await container.config_cache.invalidate()
    return out


@router.delete("/policies/{policy_id}", status_code=204, summary="Delete a routing policy")
async def delete_policy(
    policy_id: uuid.UUID, actor: Admin, session: SessionDep, container: ContainerDep
) -> Response:
    await service.delete_policy(session, policy_id, actor)
    await container.config_cache.invalidate()
    return Response(status_code=204)


@router.post(
    "/route/simulate",
    response_model=SimulateOut,
    summary="Rank models for a request without calling any",
)
async def simulate(
    body: SimulateIn, _: Viewer, container: ContainerDep, catalog: CatalogDep
) -> SimulateOut:
    return await service.simulate(GatewayService(container, catalog), body)


@router.get(
    "/quality", response_model=list[QualityCell], summary="The quality table the router ranks by"
)
async def quality(_: Viewer, session: SessionDep) -> list[QualityCell]:
    return await service.quality_table(session)


@router.put("/quality", response_model=QualityCell, summary="Set a quality score manually")
async def set_quality(
    body: QualityOverride, actor: Admin, session: SessionDep, container: ContainerDep
) -> QualityCell:
    out = await service.set_quality(session, body, actor)
    await container.config_cache.invalidate()
    return out
