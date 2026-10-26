"""The acceptance criteria from docs/PLAN.md, one test group per criterion. Routing runs against
the pinned quality table in conftest.World, so every expected choice can be worked out by hand.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI

from tests.integration.conftest import Api, World, chat

pytestmark = pytest.mark.integration

CLASSIFY = "Classify the sentiment as positive or negative: I love this product"
HARD_CODE = (
    "Implement a production-ready LRU cache class in python with get and put, "
    "handling edge cases and concurrency"
)
SIMPLE_CHAT = "Hello there, how are you today?"
MATH = "Solve 3x + 7 = 22 and show each step"


def _h(r: httpx.Response, name: str) -> str:
    return r.headers.get(f"x-smartrouter-{name}", "")


# 1. OpenAI compatibility ---------------------------------------------------------------


async def test_unmodified_openai_sdk_routes_auto(api: Api, world: World) -> None:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://t")
    client = AsyncOpenAI(api_key=world.key, base_url="http://t/v1", http_client=http)
    for model in ("auto", "auto:cost", "auto:latency", "auto:quality"):
        raw = await client.chat.completions.with_raw_response.create(
            model=model, messages=[{"role": "user", "content": SIMPLE_CHAT}]
        )
        completion = raw.parse()
        assert completion.choices[0].message.content
        assert completion.model == raw.headers["x-smartrouter-model"]
        assert raw.headers["x-smartrouter-policy"] == (
            model.split(":")[1] if ":" in model else "balanced"
        )
        assert raw.headers["x-smartrouter-request-id"].startswith("req_")
    stream = await client.chat.completions.create(
        model="auto", stream=True, messages=[{"role": "user", "content": SIMPLE_CHAT}]
    )
    text = "".join([c.choices[0].delta.content or "" async for c in stream if c.choices])
    assert text
    await http.aclose()


async def test_models_endpoint_lists_routes_and_models(api: Api, world: World) -> None:
    r = await api.client.get("/v1/models", headers={"authorization": f"Bearer {world.key}"})
    ids = {m["id"] for m in r.json()["data"]}
    assert {"auto", "auto:cost", "auto:quality", "gpt-pro", "llama-local"} <= ids


# 2. Strategies over at least three endpoints, one local ----------------------------------


async def test_cost_first_sends_simple_classification_to_the_cheapest_eligible(
    api: Api, world: World
) -> None:
    r = await api.gateway(world.key, chat(CLASSIFY, "auto:cost"))
    assert r.status_code == 200
    assert _h(r, "task") == "classification"
    assert _h(r, "model") == "llama-local"  # the local model: free and above the floor


async def test_quality_first_sends_hard_coding_to_the_best_coder(api: Api, world: World) -> None:
    await api.ok("PATCH", f"/api/v1/policies/{world.policies['quality']}", {"hard_floor_boost": 0})
    r = await api.gateway(world.key, chat(HARD_CODE, "auto:quality"))
    assert _h(r, "task") == "coding" and int(_h(r, "complexity")) >= 4
    assert _h(r, "model") == "claude-pro"


async def test_latency_first_sends_to_the_fastest_healthy_model(api: Api, world: World) -> None:
    r = await api.gateway(world.key, chat(SIMPLE_CHAT, "auto:latency"))
    assert _h(r, "model") == "gpt-fast"


async def test_policy_header_and_project_default(api: Api, world: World) -> None:
    r = await api.gateway(world.key, chat(SIMPLE_CHAT), {"x-smartrouter-policy": "latency"})
    assert _h(r, "policy") == "latency"
    await api.ok("PATCH", f"/api/v1/projects/{world.project_id}", {"default_policy": "cost"})
    r = await api.gateway(world.key, chat(SIMPLE_CHAT))
    assert _h(r, "policy") == "cost"


async def test_unknown_policy_is_a_clear_error(api: Api, world: World) -> None:
    r = await api.gateway(world.key, chat(SIMPLE_CHAT, "auto:nope"))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "policy_not_found"


# 3. Hard constraints ----------------------------------------------------------------------


TOOLS = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]


@pytest.mark.parametrize(
    ("extra", "excluded_reason"),
    [
        ({"tools": TOOLS}, "no tool calling"),
        ({"response_format": {"type": "json_object"}}, "no JSON output mode"),
    ],
)
async def test_capability_requirements_exclude_models(
    api: Api, world: World, extra: dict[str, Any], excluded_reason: str
) -> None:
    r = await api.gateway(world.key, chat(CLASSIFY, "auto:cost", **extra))
    assert r.status_code == 200
    assert _h(r, "model") != "llama-local"
    await api.drain()
    d = await api.decision(_h(r, "request-id"))
    llama = next(c for c in d["candidates"] if c["model"] == "llama-local")
    assert not llama["eligible"] and llama["reason"] == excluded_reason


async def test_context_window_is_respected(api: Api, world: World) -> None:
    long_prompt = "Classify the sentiment as positive or negative. " + "great product " * 3000
    r = await api.gateway(world.key, chat(long_prompt, "auto:cost"))
    assert r.status_code == 200 and _h(r, "model") != "llama-local"


async def test_vision_goes_only_to_a_vision_model(api: Api, world: World) -> None:
    body = {
        "model": "auto:cost",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this picture?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ],
    }
    r = await api.gateway(world.key, body)
    assert _h(r, "model") == "claude-pro"


async def test_allowed_models_are_respected(api: Api, world: World) -> None:
    await api.ok("POST", "/api/v1/policies", {"name": "only-mini", "allowed_models": ["gpt-mini"]})
    r = await api.gateway(world.key, chat(MATH, "auto:only-mini"))
    # gpt-mini is below the maths floor, but it is the only allowed model: relaxed, never escaped.
    assert _h(r, "model") == "gpt-mini"


async def test_no_eligible_model_with_reject_policy_is_422(api: Api, world: World) -> None:
    await api.ok(
        "POST",
        "/api/v1/policies",
        {"name": "picky", "quality_floor": 0.99, "on_no_eligible": "reject"},
    )
    r = await api.gateway(world.key, chat(MATH, "auto:picky"))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "no_eligible_model"
    assert (await api.mock_calls()).get("chat", 0) == 0


# 4. Quality floor -------------------------------------------------------------------------


async def test_quality_floor_is_never_crossed(api: Api, world: World) -> None:
    r = await api.gateway(world.key, chat(MATH, "auto:cost"))
    assert _h(r, "task") == "math"
    # gpt-fast (0.60), gpt-mini (0.50) and llama-local (0.40) are below the 0.70 floor.
    assert _h(r, "model") == "gpt-pro"
    await api.drain()
    d = await api.decision(_h(r, "request-id"))
    below = {c["model"] for c in d["candidates"] if "below the floor" in c["reason"]}
    assert below == {"gpt-fast", "gpt-mini", "llama-local"}
    assert not d["floor_relaxed"]


# 5. Health and fallback ---------------------------------------------------------------------


async def test_failure_falls_back_and_the_chain_is_recorded(api: Api, world: World) -> None:
    await api.fault("mock-fast", "error", count=1)
    r = await api.gateway(world.key, chat(SIMPLE_CHAT, "auto:latency"))
    assert r.status_code == 200
    assert _h(r, "fallbacks") == "gpt-fast"
    assert _h(r, "model") != "gpt-fast"
    assert _h(r, "attempts") == "2"
    await api.drain()
    d = await api.decision(_h(r, "request-id"))
    assert [a["outcome"] for a in d["fallback_chain"]] == ["error", "ok"]
    assert d["fallback_chain"][0]["model"] == "gpt-fast"


@pytest.mark.parametrize("mode", ["throttle", "timeout"])
async def test_throttling_and_timeouts_are_retryable(api: Api, world: World, mode: str) -> None:
    await api.fault("mock-fast", mode, count=1)
    r = await api.gateway(world.key, chat(SIMPLE_CHAT, "auto:latency"))
    assert r.status_code == 200 and _h(r, "model") != "gpt-fast"


async def test_with_fallback_disabled_the_error_is_returned(api: Api, world: World) -> None:
    await api.fault("mock-claude", "error")
    await api.fault("mock-pro", "error")
    r = await api.gateway(world.key, chat(HARD_CODE, "auto:strict"))
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "all_models_failed"
    stats = await api.mock_calls()
    assert sum(stats["by_model"].values()) == 1  # one attempt, no silent switch


async def test_open_circuit_is_never_chosen(api: Api, world: World) -> None:
    await api.fault("mock-fast", "error", count=3)
    for _ in range(3):
        assert (await api.gateway(world.key, chat(SIMPLE_CHAT, "auto:latency"))).status_code == 200
    health = {h["model"]: h for h in await api.ok("GET", "/api/v1/health/models")}
    assert health["gpt-fast"]["state"] == "open"
    before = (await api.mock_calls())["by_model"].get("mock-fast", 0)
    r = await api.gateway(world.key, chat(SIMPLE_CHAT, "auto:latency"))
    assert _h(r, "model") != "gpt-fast" and _h(r, "attempts") == "1"
    assert (await api.mock_calls())["by_model"].get("mock-fast", 0) == before
    await api.drain()
    d = await api.decision(_h(r, "request-id"))
    fast = next(c for c in d["candidates"] if c["model"] == "gpt-fast")
    assert "circuit open" in fast["reason"]


# 6. Breaker recovery --------------------------------------------------------------------------


async def test_breaker_recovers_through_a_half_open_probe(api: Api, world: World) -> None:
    await api.fault("mock-fast", "error", count=3)
    for _ in range(3):
        await api.gateway(world.key, chat(SIMPLE_CHAT, "auto:latency"))
    await asyncio.sleep(1.2)  # cooldown is 1 s in tests
    states = {h["model"]: h["state"] for h in await api.ok("GET", "/api/v1/health/models")}
    assert states["gpt-fast"] == "half_open"
    r = await api.gateway(world.key, chat(SIMPLE_CHAT, "auto:latency"))
    assert _h(r, "model") == "gpt-fast"  # the probe
    states = {h["model"]: h["state"] for h in await api.ok("GET", "/api/v1/health/models")}
    assert states["gpt-fast"] == "closed"


async def test_manual_reset_closes_a_circuit(api: Api, world: World) -> None:
    await api.fault("mock-fast", "error", count=3)
    for _ in range(3):
        await api.gateway(world.key, chat(SIMPLE_CHAT, "auto:latency"))
    await api.ok("POST", "/api/v1/health/models/gpt-fast/reset")
    states = {h["model"]: h["state"] for h in await api.ok("GET", "/api/v1/health/models")}
    assert states["gpt-fast"] == "closed"


# 7. Decision log --------------------------------------------------------------------------------


async def test_every_decision_is_logged_with_costs_and_savings(api: Api, world: World) -> None:
    r = await api.gateway(world.key, chat(CLASSIFY, "auto:cost"))
    await api.drain()
    d = await api.decision(_h(r, "request-id"))
    assert d["task"] == "classification" and d["complexity"] >= 1
    assert d["signals"] and d["reason"]
    assert len(d["candidates"]) == 5
    assert all({"score", "reason", "quality", "eligible"} <= set(c) for c in d["candidates"])
    assert d["selected_model"] == d["final_model"] == "llama-local"
    assert d["attempts"] == 1 and d["status"] == "SUCCESS"
    assert d["baseline_model"] == "claude-pro"
    assert Decimal(str(d["baseline_cost_usd"])) > Decimal(str(d["actual_cost_usd"]))
    assert d["latency_ms"] >= 0 and d["routing_us"] > 0
    page = await api.ok("GET", "/api/v1/decisions?task=classification")
    assert page["items"][0]["request_id"] == d["request_id"]


async def test_failed_requests_are_logged_too(api: Api, world: World) -> None:
    await api.fault("mock-pro", "error")
    r = await api.gateway(world.key, chat(SIMPLE_CHAT, "gpt-pro"))
    assert r.status_code == 502
    await api.drain()
    page = await api.ok("GET", "/api/v1/decisions?status=ERROR")
    assert page["items"][0]["final_model"] is None


async def test_feedback_is_attached_to_the_decision(api: Api, world: World) -> None:
    r = await api.gateway(world.key, chat(SIMPLE_CHAT))
    rid = _h(r, "request-id")
    fb = await api.client.post(
        "/v1/feedback",
        json={"request_id": rid, "score": 1, "comment": "good"},
        headers={"authorization": f"Bearer {world.key}"},
    )
    assert fb.status_code == 200, fb.text
    await api.drain()
    assert (await api.decision(rid))["feedback"] == 1


async def test_analytics_report_savings(api: Api, world: World) -> None:
    for _ in range(3):
        await api.gateway(world.key, chat(CLASSIFY, "auto:cost"))
    await api.drain()
    overview = await api.ok("GET", "/api/v1/analytics/overview?range=24h")
    assert overview["totals"]["requests"] == 3
    assert overview["totals"]["savings_pct"] > 90


# 8. Passthrough ----------------------------------------------------------------------------------


async def test_a_concrete_model_is_passed_through_unrouted(api: Api, world: World) -> None:
    r = await api.gateway(world.key, chat(CLASSIFY, "gpt-pro"))
    assert _h(r, "model") == "gpt-pro"
    await api.fault("mock-pro", "error")
    r = await api.gateway(world.key, chat(CLASSIFY, "gpt-pro"))
    assert r.status_code == 502  # never silently switched
    assert (await api.mock_calls())["by_model"].get("mock-fast", 0) == 0


async def test_unknown_model_is_404(api: Api, world: World) -> None:
    r = await api.gateway(world.key, chat(CLASSIFY, "gpt-9"))
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"


# 9. TokenShield -----------------------------------------------------------------------------------


async def _shielded_policy(api: Api, downgrade: bool) -> None:
    await api.ok(
        "POST",
        "/api/v1/policies",
        {
            "name": "shielded",
            "weights": {"quality": 1},
            "quality_floor": 0.7,
            "allowed_models": ["gpt-mini", "llama-local"],
            "budget_downgrade": downgrade,
        },
    )


async def test_actual_cost_comes_from_tokenshield(api: Api, world: World) -> None:
    await _shielded_policy(api, downgrade=True)
    r = await api.gateway(world.key, chat(SIMPLE_CHAT, "auto:shielded"))
    assert _h(r, "model") == "gpt-mini"
    await api.drain()
    d = await api.decision(_h(r, "request-id"))
    assert d["cost_source"] == "tokenshield"
    assert d["upstream_request_id"].startswith("req_ts")
    assert d["actual_cost_usd"] > 0


async def test_budget_refusal_downgrades_to_a_cheaper_model(api: Api, world: World) -> None:
    await _shielded_policy(api, downgrade=True)
    await api.fault("mock-mini", "budget", count=1)
    r = await api.gateway(world.key, chat(SIMPLE_CHAT, "auto:shielded"))
    assert r.status_code == 200
    assert _h(r, "model") == "llama-local"
    assert _h(r, "fallbacks") == "gpt-mini"


async def test_budget_refusal_is_returned_when_downgrade_is_off(api: Api, world: World) -> None:
    await _shielded_policy(api, downgrade=False)
    await api.fault("mock-mini", "budget", count=1)
    r = await api.gateway(world.key, chat(SIMPLE_CHAT, "auto:shielded"))
    assert r.status_code == 402
    assert r.json()["error"]["code"] == "budget_exceeded"
