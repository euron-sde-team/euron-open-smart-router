"""Gateway overhead benchmark: ``make bench`` against the running stack.

Sends the same request three ways: straight to the mock provider, through SmartRouter with a
pinned model (auth, limits, recording), and through SmartRouter with ``model="auto"`` (plus
request analysis, health lookup and ranking). The mock answers instantly for prompts marked
``[[fast]]``, so the differences are SmartRouter's own cost.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
import uuid
from collections.abc import Awaitable, Callable

import httpx

API = os.environ.get("SMARTROUTER_API_URL", "http://localhost:4230")
MOCK = os.environ.get("MOCK_LLM_URL", "http://localhost:4250")
EMAIL = os.environ.get("BENCH_EMAIL", "admin@smartrouter.local")
PASSWORD = os.environ.get("DEMO_PASSWORD") or "RouterDemo2026"
N = int(os.environ.get("BENCH_REQUESTS", "400"))
CONCURRENCY = int(os.environ.get("BENCH_CONCURRENCY", "1"))  # sequential: overhead, not queueing


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


async def timed(
    client: httpx.AsyncClient, url: str, body: dict[str, object], headers: dict[str, str]
) -> float:
    t0 = time.perf_counter()
    resp = await client.post(url, json=body, headers=headers)
    resp.raise_for_status()
    return (time.perf_counter() - t0) * 1000


async def run_batch(make: Callable[[int], Awaitable[float]], n: int) -> tuple[list[float], float]:
    sem = asyncio.Semaphore(CONCURRENCY)
    latencies: list[float] = []

    async def one(i: int) -> None:
        async with sem:
            latencies.append(await make(i))

    t0 = time.perf_counter()
    await asyncio.gather(*(one(i) for i in range(n)))
    return latencies, n / (time.perf_counter() - t0)


def line(label: str, values: list[float], rps: float | None = None) -> str:
    tail = f"   {rps:7.0f} req/s" if rps is not None else ""
    return f"{label:<22} p50 {pct(values, 0.5):6.1f} ms   p95 {pct(values, 0.95):6.1f} ms{tail}"


async def main() -> None:
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=64)) as client:
        login = await client.post(
            f"{API}/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        login.raise_for_status()
        admin = {"authorization": f"Bearer {login.json()['session_token']}"}
        tag = uuid.uuid4().hex[:6]
        project = (
            await client.post(
                f"{API}/api/v1/projects", json={"name": f"bench-{tag}"}, headers=admin
            )
        ).json()
        key = (
            await client.post(
                f"{API}/api/v1/keys",
                json={"project_id": project["id"], "name": "bench"},
                headers=admin,
            )
        ).json()
        gw = {"authorization": f"Bearer {key['key']}"}

        def body(i: int, model: str) -> dict[str, object]:
            content = f"Classify the sentiment as positive or negative: bench {tag} {i} [[fast]]"
            return {"model": model, "messages": [{"role": "user", "content": content}]}

        # Warm up connections, the catalog cache and the health snapshot.
        await run_batch(
            lambda i: timed(client, f"{API}/v1/chat/completions", body(i, "auto"), gw), 40
        )

        direct, direct_rps = await run_batch(
            lambda i: timed(client, f"{MOCK}/v1/chat/completions", body(i, "mock-fast"), {}), N
        )
        pinned, pinned_rps = await run_batch(
            lambda i: timed(client, f"{API}/v1/chat/completions", body(i, "gpt-fast"), gw), N
        )
        routed, routed_rps = await run_batch(
            lambda i: timed(client, f"{API}/v1/chat/completions", body(i, "auto"), gw), N
        )
        base = statistics.median(direct)
        print(f"requests per run: {N}, concurrency: {CONCURRENCY}")
        print(line("provider direct", direct, direct_rps))
        print(line("gateway, pinned model", pinned, pinned_rps))
        print(line("gateway, model auto", routed, routed_rps))
        print(line("overhead, pinned", [x - base for x in pinned]))
        print(line("overhead, auto", [x - base for x in routed]))
        await client.patch(
            f"{API}/api/v1/projects/{project['id']}", json={"archived": True}, headers=admin
        )


if __name__ == "__main__":
    asyncio.run(main())
