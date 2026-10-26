"""FastAPI dependencies shared across domains."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from smartrouter.core.container import Container


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


async def get_session(
    container: Annotated[Container, Depends(get_container)],
) -> AsyncIterator[AsyncSession]:
    async with container.db.sessionmaker() as session:
        yield session


ContainerDep = Annotated[Container, Depends(get_container)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str | None:
    """The caller's address for throttling and audit. Only addresses added by the trusted
    proxies in front of the API count (TRUSTED_PROXY_HOPS): the leftmost X-Forwarded-For
    entries are written by the client and can say anything."""
    peer = request.client.host if request.client else None
    hops = request.app.state.container.settings.trusted_proxy_hops
    if hops <= 0:
        return peer
    chain = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    # Each trusted proxy appends the address it saw; the outermost one saw the real client.
    return chain[-hops] if len(chain) >= hops else (chain[0] if chain else peer)
