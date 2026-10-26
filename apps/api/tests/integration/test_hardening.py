"""Production hardening: config cache invalidation, endpoint credential safety, the one default
policy, feedback targets, rate-limit passthrough, stream fallback, the probe lock, circuit
alerts from probes, and benchmark runs that can never be left stuck."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from redis.exceptions import RedisError
from sqlalchemy import select, text, update

from smartrouter.benchmarks.models import BenchmarkRun
from smartrouter.core.cache import VERSION_KEY
from smartrouter.worker import Worker
from tests.integration.conftest import Api, World, chat

pytestmark = pytest.mark.integration


# Caching layer: a change made through the admin API reaches every gateway instance.


async def test_admin_writes_invalidate_the_catalog_cluster_wide(api: Api, world: World) -> None:
    redis = api.app.state.container.redis
    before = await redis.get(VERSION_KEY)
    r = await api.gateway(world.key, chat("Hello there", "auto:latency"))
    assert r.headers["x-smartrouter-model"] == "gpt-fast"
    await api.ok("PATCH", f"/api/v1/models/{world.models['gpt-fast']}", {"enabled": False})
    assert await redis.get(VERSION_KEY) != before  # the stamp every instance compares
    await asyncio.sleep(1.1)  # ConfigCache checks the stamp at most once a second
    r = await api.gateway(world.key, chat("Hello there", "auto:latency"))
    assert r.headers["x-smartrouter-model"] != "gpt-fast"


# Endpoints: URLs and stored credentials.


async def test_metadata_urls_are_refused(api: Api, world: World) -> None:
    r = await api.post(
        "/api/v1/providers",
        {"name": "evil", "kind": "openai", "base_url": "http://169.254.169.254/latest"},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "endpoint_url_blocked"
    r = await api.client.patch(
        f"/api/v1/providers/{world.endpoints['openai']}",
        json={"base_url": "http://169.254.169.254/latest", "api_key": "sk-new"},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "endpoint_url_blocked"


async def test_moving_an_endpoint_to_another_host_needs_its_key_again(
    api: Api, world: World
) -> None:
    ep = world.endpoints["openai"]
    r = await api.client.patch(
        f"/api/v1/providers/{ep}", json={"base_url": "http://localhost:9/v1"}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "api_key_required"
    same_host = api.mock_url + "/v1/"
    await api.ok("PATCH", f"/api/v1/providers/{ep}", {"base_url": same_host})
    await api.ok(
        "PATCH",
        f"/api/v1/providers/{ep}",
        {"base_url": "http://localhost:9/v1", "api_key": "sk-new-key"},
    )


# Policies: the database allows exactly one default.


async def test_one_default_policy_even_under_concurrent_updates(api: Api, world: World) -> None:
    ids = [world.policies[n] for n in ("cost", "latency", "quality")]
    results = await asyncio.gather(
        *(api.client.patch(f"/api/v1/policies/{i}", json={"is_default": True}) for i in ids)
    )
    assert any(r.status_code == 200 for r in results)
    policies = await api.ok("GET", "/api/v1/policies")
    assert sum(1 for p in policies if p["is_default"]) == 1
    engine = api.app.state.container.db.engine
    async with engine.begin() as conn:
        with pytest.raises(Exception, match="uq_routing_policies_single_default"):
            await conn.execute(text("UPDATE routing_policies SET is_default = true"))


# Feedback.


async def test_feedback_needs_a_real_request_and_works_before_the_drain(
    api: Api, world: World
) -> None:
    headers = {"authorization": f"Bearer {world.key}"}
    unknown = await api.client.post(
        "/v1/feedback", json={"request_id": "req_nope", "score": 1}, headers=headers
    )
    assert unknown.status_code == 404
    r = await api.gateway(world.key, chat("Hello there"))
    rid = r.headers["x-smartrouter-request-id"]
    # No drain yet: the decision row does not exist, the recorder's marker does.
    ok = await api.client.post(
        "/v1/feedback", json={"request_id": rid, "score": -1}, headers=headers
    )
    assert ok.status_code == 200, ok.text
    await api.drain()
    assert (await api.decision(rid))["feedback"] == -1


async def test_feedback_is_rate_limited_per_key(api: Api, world: World) -> None:
    await api.ok("PATCH", f"/api/v1/keys/{world.key_id}", {"rpm_limit": 2})
    rid = (await api.gateway(world.key, chat("Hello there"))).headers["x-smartrouter-request-id"]
    headers = {"authorization": f"Bearer {world.key}"}
    codes = [
        (
            await api.client.post(
                "/v1/feedback", json={"request_id": rid, "score": 1}, headers=headers
            )
        ).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]


# Upstream errors.


async def test_a_pinned_model_rate_limited_upstream_returns_429(api: Api, world: World) -> None:
    await api.fault("mock-pro", "throttle", count=1)
    r = await api.gateway(world.key, chat("Hello there", "gpt-pro"))
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "upstream_rate_limited"
    assert r.headers["retry-after"] == "1"


async def test_a_stream_falls_back_before_the_first_chunk(api: Api, world: World) -> None:
    await api.fault("mock-fast", "error", count=1)
    async with api.client.stream(
        "POST",
        "/v1/chat/completions",
        json=chat("Hello there", "auto:latency", stream=True),
        headers={"authorization": f"Bearer {world.key}"},
    ) as r:
        assert r.status_code == 200
        assert r.headers["x-smartrouter-fallbacks"] == "gpt-fast"
        body = "".join([chunk async for chunk in r.aiter_text()])
    assert "data: [DONE]" in body


# Health.


async def test_only_one_request_probes_a_half_open_circuit(api: Api, world: World) -> None:
    await api.fault("mock-fast", "error", count=3)
    for _ in range(3):
        await api.gateway(world.key, chat("Hello there", "auto:latency"))
    await asyncio.sleep(1.2)  # cooldown: half open
    # The probe fails, so the circuit reopens: no request but the single probe touches it.
    await api.fault("mock-fast", "error", count=1)
    before = (await api.mock_calls())["by_model"].get("mock-fast", 0)
    rs = await asyncio.gather(
        *(api.gateway(world.key, chat("Hello there", "auto:latency")) for _ in range(5))
    )
    assert all(r.status_code == 200 for r in rs)
    assert (await api.mock_calls())["by_model"].get("mock-fast", 0) - before == 1
    assert not any(r.headers["x-smartrouter-model"] == "gpt-fast" for r in rs)


async def test_a_circuit_opened_by_probes_raises_an_alert(api: Api, world: World) -> None:
    await api.ok(
        "PATCH",
        f"/api/v1/providers/{world.endpoints['local']}",
        {"base_url": "http://127.0.0.1:9/v1", "api_key": "sk-test-only-123456"},
    )
    worker = Worker(api.app.state.container)
    for _ in range(3):
        await worker.probe()
    alerts = await api.ok("GET", "/api/v1/alerts")
    assert any("llama-local" in a["message"] for a in alerts["items"])


# Benchmarks.


async def test_a_run_abandoned_by_a_dead_worker_is_marked_failed(api: Api, world: World) -> None:
    queued = await api.ok("POST", "/api/v1/benchmarks/runs", {"policies": []}, status=202)
    sessionmaker = api.app.state.container.db.sessionmaker
    async with sessionmaker() as session, session.begin():
        await session.execute(
            update(BenchmarkRun)
            .where(BenchmarkRun.id == queued["id"])
            .values(status="running", started_at=datetime.now(UTC) - timedelta(hours=3))
        )
    assert await Worker(api.app.state.container).fail_stale_runs() == 1
    run = await api.ok("GET", f"/api/v1/benchmarks/runs/{queued['id']}")
    assert run["status"] == "failed" and "stopped" in run["error"]


async def test_a_queue_outage_never_leaves_a_queued_run_behind(
    api: Api, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis = api.app.state.container.redis

    async def broken(*_a: object, **_k: object) -> None:
        raise RedisError("queue down")

    monkeypatch.setattr(redis, "rpush", broken)
    r = await api.post("/api/v1/benchmarks/runs", {"policies": []})
    assert r.status_code == 503 and r.json()["error"]["code"] == "dependency_unavailable"
    monkeypatch.undo()
    async with api.app.state.container.db.sessionmaker() as session:
        statuses = (await session.scalars(select(BenchmarkRun.status))).all()
    assert "queued" not in statuses


async def test_an_expired_results_file_is_a_clear_404(api: Api, world: World) -> None:
    from smartrouter.benchmarks.router import QUEUE_KEY
    from smartrouter.benchmarks.runner import BenchmarkRunner

    queued = await api.ok(
        "POST", "/api/v1/benchmarks/runs", {"models": ["gpt-fast"], "policies": []}, status=202
    )
    container = api.app.state.container
    await container.redis.lpop(QUEUE_KEY)
    import uuid

    await BenchmarkRunner(container).run(uuid.UUID(queued["id"]), ["gpt-fast"], [])
    async with container.db.sessionmaker() as session, session.begin():
        await session.execute(
            update(BenchmarkRun)
            .where(BenchmarkRun.id == queued["id"])
            .values(finished_at=datetime.now(UTC) - timedelta(days=400))
        )
    r = await api.client.get(f"/api/v1/benchmarks/runs/{queued['id']}/cases")
    assert r.status_code == 404 and "expired" in r.json()["error"]["message"]


async def test_feedback_still_works_when_redis_is_down(
    api: Api, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = (await api.gateway(world.key, chat("Hello there"))).headers["x-smartrouter-request-id"]
    await api.drain()  # the decision row exists; only Redis is down

    async def broken(*_a: object, **_k: object) -> None:
        raise RedisError("redis down")

    from smartrouter.limits.limiter import Limiter

    monkeypatch.setattr(Limiter, "hit", broken)
    monkeypatch.setattr(api.app.state.container.redis, "get", broken)
    r = await api.client.post(
        "/v1/feedback",
        json={"request_id": rid, "score": 1},
        headers={"authorization": f"Bearer {world.key}"},
    )
    assert r.status_code == 200, r.text
