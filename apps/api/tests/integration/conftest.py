"""Integration harness: the real app in-process against real Postgres and Redis, with the
mock LLM served over real HTTP from a background thread.

    make test-integration      (starts Postgres and Redis containers if needed)

Safety: the suite truncates every table, so it refuses any database whose name does not
contain "test", and any non-local host unless ALLOW_REMOTE_TEST_DB=true.
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
import uvicorn
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mockllm import app as mockllm
from smartrouter.core.config import Settings
from smartrouter.main import create_app

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://smartrouter:smartrouter@localhost:4242/smartrouter_test",
)
TEST_REDIS = os.environ.get("TEST_REDIS_URL", "redis://localhost:4279/15")
OWNER = {"email": "owner@example.test", "password": "Owner-Pass-123"}
TABLES = (
    "routing_decisions, decision_feedback, decision_rollups_hourly, benchmark_results, "
    "benchmark_runs, quality_scores, alerts, audit_log, api_keys, projects, routing_policies, "
    "models, providers, teams, users"
)


def _guard(url: str) -> None:
    parsed = urlparse(url.replace("+asyncpg", ""))
    if "test" not in (parsed.path or ""):
        raise RuntimeError(
            f"refusing to run against {parsed.path!r}: database name must contain 'test'"
        )
    if (
        parsed.hostname not in ("localhost", "127.0.0.1", "postgres")
        and os.environ.get("ALLOW_REMOTE_TEST_DB") != "true"
    ):
        raise RuntimeError("refusing a non-local test database (set ALLOW_REMOTE_TEST_DB=true)")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def mock_url() -> Iterator[str]:
    # Same interpreter as the tests: no simulated latency, and faults are set over HTTP.
    mockllm.LATENCY_SCALE = 0
    port = _free_port()
    config = uvicorn.Config(mockllm.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
async def database() -> AsyncIterator[str]:
    _guard(TEST_DB)
    admin_url = TEST_DB.rsplit("/", 1)[0] + "/postgres"
    name = TEST_DB.rsplit("/", 1)[1]
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await engine.dispose()
    from smartrouter.migrations.runner import upgrade

    await asyncio.to_thread(upgrade, TEST_DB)
    yield TEST_DB


@pytest.fixture
def settings(database: str, tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database,
        redis_url=TEST_REDIS,
        session_secret="test-only-session-secret-with-32-plus-bytes",
        api_key_pepper="test-only-api-key-pepper-with-32-plus-bytes",
        local_master_key="test-only-master-key-with-32-plus-bytes-ok",
        local_storage_dir=str(tmp_path / "exports"),
        upstream_timeout_s=2,
        breaker_failure_threshold=3,
        breaker_min_samples=1000,  # trip on consecutive failures only, so tests are exact
        breaker_cooldown_s=1,
        # The option matrix (make test-matrix) reruns the suite against Floci-backed KMS and S3.
        key_mode=os.environ.get("TEST_KEY_MODE", "local"),
        storage=os.environ.get("TEST_STORAGE", "local"),
        aws_endpoint_url=os.environ.get("TEST_AWS_ENDPOINT"),
        s3_public_endpoint=os.environ.get("TEST_AWS_ENDPOINT"),
        s3_force_path_style=True,
        kms_key_id=os.environ.get("TEST_KMS_KEY_ID", "alias/smartrouter-local"),
        exports_bucket=os.environ.get("TEST_EXPORTS_BUCKET", "smartrouter-local-exports"),
    )


async def _reset(settings: Settings, mock_url: str) -> None:
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    await engine.dispose()
    from redis.asyncio import Redis

    redis = Redis.from_url(settings.redis_url)
    await redis.flushdb()
    await redis.aclose()
    async with httpx.AsyncClient() as client:
        await client.post(f"{mock_url}/stats/reset")
        await client.delete(f"{mock_url}/admin/faults")


@dataclass
class Api:
    """A signed-in admin client plus helpers for common setup."""

    client: httpx.AsyncClient
    app: FastAPI
    mock_url: str

    async def mock_calls(self) -> dict[str, Any]:
        async with httpx.AsyncClient() as c:
            data: dict[str, Any] = (await c.get(f"{self.mock_url}/stats")).json()
            return data

    async def fault(self, model: str, mode: str, count: int | None = None) -> None:
        async with httpx.AsyncClient() as c:
            body: dict[str, Any] = {"model": model, "mode": mode}
            if count is not None:
                body["count"] = count
            (await c.post(f"{self.mock_url}/admin/faults", json=body)).raise_for_status()

    async def clear_faults(self) -> None:
        async with httpx.AsyncClient() as c:
            await c.delete(f"{self.mock_url}/admin/faults")

    async def post(self, path: str, json: Any = None, **kw: Any) -> httpx.Response:
        return await self.client.post(path, json=json, **kw)

    async def drain(self) -> int:
        """Write buffered decisions to Postgres, as the worker does continuously."""
        from smartrouter.worker import Worker

        worker = Worker(self.app.state.container)
        total = 0
        while written := await worker.drain():
            total += written
        return total

    async def ok(self, method: str, path: str, json: Any = None, status: int | None = None) -> Any:
        await self.drain()
        resp = await self.client.request(method, path, json=json)
        expected = status or (201 if method == "POST" and resp.status_code == 201 else 200)
        assert resp.status_code == expected, f"{method} {path}: {resp.status_code} {resp.text}"
        return resp.json() if resp.content else None

    async def fetch(self, url: str) -> httpx.Response:
        """GET a download link: presigned S3 URLs are real HTTP, API paths are in-process."""
        if url.startswith("http"):
            async with httpx.AsyncClient() as client:
                return await client.get(url)
        return await self.client.get(url)

    async def gateway(
        self, key: str, body: dict[str, Any], headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return await self.client.post(
            "/v1/chat/completions",
            json=body,
            headers={"authorization": f"Bearer {key}", **(headers or {})},
        )

    async def decision(self, request_id: str) -> dict[str, Any]:
        out: dict[str, Any] = await self.ok("GET", f"/api/v1/decisions/{request_id}")
        return out


async def _bootstrap_owner(settings: Settings) -> None:
    from smartrouter.core.db import Database
    from smartrouter.core.security import hash_password
    from smartrouter.users.models import User

    db = Database(settings.database_url, pool_size=1, max_overflow=0)
    async with db.sessionmaker() as session:
        session.add(
            User(
                email=OWNER["email"],
                name="Owner",
                role="owner",
                password_hash=hash_password(OWNER["password"]),
            )
        )
        await session.commit()
    await db.dispose()


@pytest.fixture
async def app(settings: Settings, mock_url: str) -> AsyncIterator[FastAPI]:
    await _reset(settings, mock_url)
    await _bootstrap_owner(settings)
    application = create_app(settings)
    async with LifespanManager(application, startup_timeout=60, shutdown_timeout=30):
        yield application


@pytest.fixture
async def api(app: FastAPI, mock_url: str) -> AsyncIterator[Api]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", timeout=30
    ) as client:
        resp = await client.post("/api/v1/auth/login", json=OWNER)
        assert resp.status_code == 200, resp.text
        client.headers["authorization"] = f"Bearer {resp.json()['session_token']}"
        yield Api(client, app, mock_url)


# alias: (endpoint, upstream, tier, (input, output) USD per 1M, latency prior ms, extras)
MODELS: dict[str, tuple[str, str, str, tuple[str, str], int, dict[str, Any]]] = {
    "gpt-fast": ("openai", "mock-fast", "fast", ("0.10", "0.40"), 300, {}),
    "gpt-mini": ("tokenshield", "mock-mini", "low-cost", ("0.05", "0.20"), 450, {}),
    "gpt-pro": ("openai", "mock-pro", "advanced", ("3.00", "12.00"), 1600, {}),
    "claude-pro": (
        "anthropic",
        "mock-claude",
        "advanced",
        ("3.00", "15.00"),
        1800,
        {"context_window": 200000, "supports_vision": True},
    ),
    "llama-local": (
        "local",
        "mock-llama",
        "local",
        ("0", "0"),
        900,
        {"context_window": 4096, "supports_tools": False, "supports_json": False},
    ),
}
# Pinned quality (manual scores) so routing assertions do not depend on a benchmark run.
QUALITY: dict[str, dict[str, float]] = {
    "gpt-fast": {"classification": 0.92, "coding": 0.55, "math": 0.6, "chat": 0.85},
    "gpt-mini": {"classification": 0.90, "coding": 0.45, "math": 0.5, "chat": 0.80},
    "gpt-pro": {"classification": 0.97, "coding": 0.90, "math": 0.95, "chat": 0.95},
    "claude-pro": {"classification": 0.97, "coding": 0.97, "math": 0.92, "chat": 0.95},
    "llama-local": {"classification": 0.88, "coding": 0.40, "math": 0.4, "chat": 0.75},
}


@dataclass
class World:
    """A configured gateway: 4 endpoints (one local, one TokenShield), 5 models with pinned
    quality, the standard policies, a project and a key."""

    project_id: str
    key: str
    key_id: str
    endpoints: dict[str, str]
    models: dict[str, str]
    policies: dict[str, str] = field(default_factory=dict)


ENDPOINTS = {
    "openai": ("openai", "/v1"),
    "anthropic": ("anthropic", ""),
    "local": ("openai", "/v1"),
    "tokenshield": ("tokenshield", "/tokenshield/v1"),
}
POLICIES: list[dict[str, Any]] = [
    {"name": "balanced", "strategy": "balanced", "quality_floor": 0.7, "is_default": True},
    {"name": "cost", "strategy": "cost", "quality_floor": 0.7, "budget_downgrade": True},
    {"name": "latency", "strategy": "latency", "quality_floor": 0.7},
    {"name": "quality", "strategy": "quality", "quality_floor": 0.85},
    {"name": "strict", "strategy": "quality", "quality_floor": 0.85, "allow_fallback": False},
]


@pytest.fixture
async def world(api: Api) -> World:
    endpoints: dict[str, str] = {}
    for name, (kind, path) in ENDPOINTS.items():
        ep = await api.ok(
            "POST",
            "/api/v1/providers",
            {
                "name": f"Mock {name}",
                "kind": kind,
                "base_url": f"{api.mock_url}{path}",
                "api_key": "sk-test-only-123456",
                "timeout_s": 5,  # generous: a slow run must not turn a test into a timeout fallback
                "simulated": True,
            },
        )
        endpoints[name] = ep["id"]
    models: dict[str, str] = {}
    for alias, (ep, upstream, tier, price, latency, extras) in MODELS.items():
        m = await api.ok(
            "POST",
            "/api/v1/models",
            {
                "alias": alias,
                "provider_id": endpoints[ep],
                "upstream_model": upstream,
                "tier": tier,
                "input_per_m": price[0],
                "output_per_m": price[1],
                "latency_prior_ms": latency,
                "max_output_tokens": 4096,
                **extras,
            },
        )
        models[alias] = m["id"]
    for alias, scores in QUALITY.items():
        for task, score in scores.items():
            for band in ("low", "high"):
                await api.ok(
                    "PUT",
                    "/api/v1/quality",
                    {
                        "model_alias": alias,
                        "task": task,
                        "band": band,
                        "score": score if band == "low" else round(score - 0.05, 2),
                    },
                )
    policies = {}
    for p in POLICIES:
        out = await api.ok("POST", "/api/v1/policies", p)
        policies[p["name"]] = out["id"]
    project = await api.ok("POST", "/api/v1/projects", {"name": "Alpha Tutor"})
    key = await api.ok("POST", "/api/v1/keys", {"project_id": project["id"], "name": "prod"})
    return World(project["id"], key["key"], key["id"], endpoints, models, policies)


def chat(
    content: str = "What is photosynthesis?", model: str = "auto", **extra: Any
) -> dict[str, Any]:
    return {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": content}],
        **extra,
    }
