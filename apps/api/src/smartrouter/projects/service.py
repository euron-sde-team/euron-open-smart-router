from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from smartrouter.audit.service import Actor, audit
from smartrouter.core.errors import AppError, NotFoundError
from smartrouter.core.text import slugify
from smartrouter.keys.models import ApiKey
from smartrouter.projects.models import Project
from smartrouter.projects.schemas import ProjectIn, ProjectOut, ProjectUpdate
from smartrouter.routing.models import RoutingPolicy
from smartrouter.teams.models import Team


async def list_projects(session: AsyncSession, include_archived: bool = False) -> list[ProjectOut]:
    keys = (
        select(ApiKey.project_id, func.count().label("n"))
        .where(ApiKey.revoked_at.is_(None))
        .group_by(ApiKey.project_id)
        .subquery()
    )
    stmt = (
        select(Project, Team.name, func.coalesce(keys.c.n, 0), RoutingPolicy.name)
        .outerjoin(Team, Team.id == Project.team_id)
        .outerjoin(keys, keys.c.project_id == Project.id)
        .outerjoin(RoutingPolicy, RoutingPolicy.id == Project.default_policy_id)
        .order_by(Project.name)
    )
    if not include_archived:
        stmt = stmt.where(Project.archived.is_(False))
    rows = (await session.execute(stmt)).all()
    return [
        ProjectOut.model_validate(p).model_copy(
            update={"team_name": team, "key_count": n, "default_policy": policy}
        )
        for p, team, n, policy in rows
    ]


async def get_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def _validate(session: AsyncSession, team_id: uuid.UUID | None) -> None:
    if team_id and not await session.get(Team, team_id):
        raise AppError("Team does not exist", code="unknown_team")


async def _policy_id(session: AsyncSession, name: str | None) -> uuid.UUID | None:
    if not name:
        return None
    pid = await session.scalar(select(RoutingPolicy.id).where(RoutingPolicy.name == name))
    if pid is None:
        raise AppError(f"Routing policy '{name}' does not exist", code="unknown_policy")
    return pid


async def project_out(session: AsyncSession, project: Project) -> ProjectOut:
    name = (
        await session.scalar(
            select(RoutingPolicy.name).where(RoutingPolicy.id == project.default_policy_id)
        )
        if project.default_policy_id
        else None
    )
    return ProjectOut.model_validate(project).model_copy(update={"default_policy": name})


async def _unique_slug(session: AsyncSession, name: str) -> str:
    base = slugify(name)
    slug, i = base, 2
    while await session.scalar(select(Project.id).where(Project.slug == slug)):
        slug, i = f"{base}-{i}", i + 1
    return slug


async def create_project(session: AsyncSession, data: ProjectIn, actor: Actor) -> ProjectOut:
    await _validate(session, data.team_id)
    project = Project(
        name=data.name,
        description=data.description,
        team_id=data.team_id,
        default_policy_id=await _policy_id(session, data.default_policy),
        slug=await _unique_slug(session, data.name),
    )
    session.add(project)
    await session.flush()
    audit(session, actor, "project.created", "project", project.id, name=project.name)
    await session.commit()
    return await project_out(session, project)


async def update_project(
    session: AsyncSession, project_id: uuid.UUID, data: ProjectUpdate, actor: Actor
) -> ProjectOut:
    project = await get_project(session, project_id)
    changes = data.model_dump(exclude_unset=True)
    await _validate(session, changes.get("team_id"))
    if "default_policy" in changes:
        project.default_policy_id = await _policy_id(session, changes.pop("default_policy"))
    for field, value in changes.items():
        setattr(project, field, value)
    audit(
        session,
        actor,
        "project.updated",
        "project",
        project.id,
        **{k: str(v) for k, v in data.model_dump(exclude_unset=True).items()},
    )
    await session.commit()
    return await project_out(session, project)
