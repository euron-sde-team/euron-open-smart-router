"""Acceptance criterion 10: the reproducible benchmark. Routed policies save cost against the
always-best baseline with quality inside the policy tolerance; results are stored and feed the
quality table the router uses."""

from __future__ import annotations

import gzip
import uuid
from typing import Any

import orjson
import pytest

from smartrouter.benchmarks.router import QUEUE_KEY
from smartrouter.benchmarks.runner import BenchmarkRunner
from tests.integration.conftest import Api, World

pytestmark = pytest.mark.integration


async def _run(api: Api, policies: list[str]) -> dict[str, Any]:
    queued = await api.ok("POST", "/api/v1/benchmarks/runs", {"policies": policies}, status=202)
    container = api.app.state.container
    job = orjson.loads(await container.redis.lpop(QUEUE_KEY))
    assert job["run_id"] == queued["id"]
    await BenchmarkRunner(container).run(uuid.UUID(job["run_id"]), None, policies)
    detail: dict[str, Any] = await api.ok("GET", f"/api/v1/benchmarks/runs/{queued['id']}")
    return detail


async def test_benchmark_shows_savings_within_quality_tolerance(api: Api, world: World) -> None:
    await api.ok("PATCH", f"/api/v1/policies/{world.policies['balanced']}", {"quality_floor": 0.8})
    run = await _run(api, ["balanced", "quality"])
    assert run["status"] == "done"
    summary = run["summary"]
    assert summary["baseline"] == "claude-pro"
    assert summary["cases"] >= 160
    quality, balanced = summary["policies"]["quality"], summary["policies"]["balanced"]
    # Quality-first: the baseline's quality, cheaper.
    assert quality["quality_delta"] >= -0.02 and quality["savings_pct"] > 0
    # Balanced: real savings, quality within 5 points of always using the best model.
    assert balanced["savings_pct"] >= 10 and balanced["quality_delta"] >= -0.05
    assert set(balanced["model_mix"]) - {"claude-pro"}  # it really routed elsewhere

    # Results are stored per target, task and band.
    targets = {(r["target_type"], r["target"]) for r in run["results"]}
    assert ("model", "gpt-fast") in targets and ("policy", "balanced") in targets

    # The run replaced the pinned manual scores and recorded how far the analyzer is trusted.
    cells = await api.ok("GET", "/api/v1/quality")
    assert cells and all(c["source"] == "benchmark" for c in cells)
    assert summary["calibration"]
    # Phase 2 decisions are logged as benchmark traffic, kept out of the dashboards.
    page = await api.ok("GET", "/api/v1/decisions?source=benchmark&limit=5")
    assert page["items"]
    overview = await api.ok("GET", "/api/v1/analytics/overview?range=24h")
    assert overview["totals"]["requests"] == 0


async def test_benchmark_is_reproducible(api: Api, world: World) -> None:
    first = (await _run(api, ["quality"]))["summary"]
    second = (await _run(api, ["quality"]))["summary"]

    # Quality and cost are deterministic; measured latency is wall clock and varies.
    def stable(block: dict[str, Any]) -> dict[str, tuple[float, float]]:
        return {k: (v["quality"], v["cost_usd"]) for k, v in block.items()}

    assert stable(first["models"]) == stable(second["models"])
    assert stable(first["policies"]) == stable(second["policies"])


async def test_every_graded_answer_is_downloadable(api: Api, world: World) -> None:
    run = await _run(api, ["balanced"])
    link = await api.ok("GET", f"/api/v1/benchmarks/runs/{run['id']}/cases")
    cases = run["summary"]["cases"]
    assert link["rows"] == cases * (len(run["summary"]["models"]) + 1)
    resp = await api.fetch(link["url"])
    assert resp.status_code == 200
    lines = gzip.decompress(resp.content).decode().strip().splitlines()
    assert len(lines) == link["rows"]
    first = orjson.loads(lines[0])
    assert {"target_type", "target", "case", "passed", "model", "answer"} <= set(first)
    policy_rows = [orjson.loads(x) for x in lines if '"target_type":"policy"' in x]
    assert {r["target"] for r in policy_rows} == {"balanced"}


async def test_missing_results_file_is_a_clear_404(api: Api, world: World) -> None:
    queued = await api.ok("POST", "/api/v1/benchmarks/runs", {"policies": []}, status=202)
    r = await api.client.get(f"/api/v1/benchmarks/runs/{queued['id']}/cases")
    assert r.status_code == 404 and "per-case" in r.json()["error"]["message"]
