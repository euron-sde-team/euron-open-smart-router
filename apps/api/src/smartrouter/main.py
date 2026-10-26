"""Application factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from smartrouter import __version__
from smartrouter.alerts.router import router as alerts_router
from smartrouter.audit.router import router as audit_router
from smartrouter.auth.router import router as auth_router
from smartrouter.benchmarks.router import router as benchmarks_router
from smartrouter.core.config import Settings, get_settings
from smartrouter.core.container import Container
from smartrouter.core.errors import install_error_handlers
from smartrouter.core.logs import configure_logging
from smartrouter.core.middleware import RequestContextMiddleware
from smartrouter.core.tracing import configure_tracing
from smartrouter.decisions.router import router as decisions_router
from smartrouter.gateway.router import router as gateway_router
from smartrouter.gateway.tokens import warm_up
from smartrouter.health.router import router as health_router
from smartrouter.keys.router import router as keys_router
from smartrouter.projects.router import router as projects_router
from smartrouter.providers.router import router as providers_router
from smartrouter.routing.router import router as routing_router
from smartrouter.system.router import router as system_router
from smartrouter.teams.router import router as teams_router
from smartrouter.users.router import router as users_router

DESCRIPTION = """
Self-hosted, OpenAI-compatible model router. Point any OpenAI SDK at `/v1` with a SmartRouter
key and send `model="auto"` (or `auto:<policy>`): each request is analysed, the eligible models
are ranked by cost, latency and benchmark quality under your policy, the best one answers, and
a controlled fallback takes over if it fails. Every decision is logged and explained.

The `/api/v1` endpoints power the dashboard and are available to automation with a session
token (`POST /api/v1/auth/login`).
"""


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container or Container.build(settings)
        await asyncio.to_thread(warm_up)
        try:
            yield
        finally:
            if container is None:
                await app.state.container.close()

    app = FastAPI(
        title="Euron Open SmartRouter",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    install_error_handlers(app)
    for router in (
        gateway_router,
        auth_router,
        users_router,
        teams_router,
        projects_router,
        keys_router,
        providers_router,
        routing_router,
        health_router,
        decisions_router,
        benchmarks_router,
        alerts_router,
        audit_router,
        system_router,
    ):
        app.include_router(router)

    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "authorization",
            "content-type",
            "x-smartrouter-csrf",
            "x-smartrouter-policy",
            "x-smartrouter-task",
            "x-smartrouter-complexity",
            "x-request-id",
        ],
        expose_headers=[
            "x-request-id",
            "x-smartrouter-request-id",
            "x-smartrouter-model",
            "x-smartrouter-policy",
            "x-smartrouter-strategy",
            "x-smartrouter-task",
            "x-smartrouter-complexity",
            "x-smartrouter-attempts",
            "x-smartrouter-fallbacks",
            "x-smartrouter-reason",
            "x-smartrouter-cost-usd",
            "x-smartrouter-estimated-cost-usd",
            "x-smartrouter-baseline-model",
            "x-smartrouter-baseline-cost-usd",
            "retry-after",
        ],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)
    return app
