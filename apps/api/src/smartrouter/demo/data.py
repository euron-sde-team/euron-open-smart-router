"""The demo organisation: endpoints, models, policies, projects and the prompts each project
sends. Prices are illustrative, not any provider's actual pricing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EndpointSpec:
    name: str
    kind: str
    path: str  # appended to the mock LLM URL


@dataclass(frozen=True)
class ModelSpec:
    alias: str
    endpoint: str
    upstream: str
    tier: str
    price: tuple[str, str]
    latency_ms: int
    description: str
    context: int = 128000
    tools: bool = True


@dataclass(frozen=True)
class ProjectSpec:
    team: str
    description: str
    policy: str | None
    daily: int
    prompts: tuple[str, ...]


ENDPOINTS = (
    EndpointSpec("OpenAI (mock)", "openai", "/v1"),
    EndpointSpec("Anthropic (mock)", "anthropic", ""),
    EndpointSpec("Local inference (mock)", "openai", "/v1"),
    EndpointSpec("TokenShield gateway (mock)", "tokenshield", "/tokenshield/v1"),
)

MODELS = (
    ModelSpec(
        "gpt-fast",
        "OpenAI (mock)",
        "mock-fast",
        "fast",
        ("0.10", "0.40"),
        300,
        "Quick general model for short answers and classification",
    ),
    ModelSpec(
        "gpt-mini",
        "TokenShield gateway (mock)",
        "mock-mini",
        "low-cost",
        ("0.05", "0.20"),
        450,
        "Cheapest hosted model, reached through TokenShield so its spend counts against budgets",
    ),
    ModelSpec(
        "gpt-pro",
        "OpenAI (mock)",
        "mock-pro",
        "advanced",
        ("3.00", "12.00"),
        1600,
        "Strong reasoning and maths",
    ),
    ModelSpec(
        "claude-pro",
        "Anthropic (mock)",
        "mock-claude",
        "advanced",
        ("3.00", "15.00"),
        1800,
        "Best at code review and long documents",
        context=200000,
    ),
    ModelSpec(
        "llama-local",
        "Local inference (mock)",
        "mock-llama",
        "local",
        ("0", "0"),
        900,
        "Self-hosted model: free per token, good enough for simple work",
        context=32768,
        tools=False,
    ),
)

POLICIES: tuple[dict[str, Any], ...] = (
    {
        "name": "balanced",
        "description": "Default: weighs cost, speed and quality, never below a 0.8 quality score",
        "strategy": "balanced",
        "quality_floor": 0.8,
        "is_default": True,
    },
    {
        "name": "cost",
        "description": "Cheapest model that clears the floor; downgrades when a budget refuses",
        "strategy": "cost",
        "quality_floor": 0.7,
        "budget_downgrade": True,
    },
    {
        "name": "latency",
        "description": "Fastest model that clears the floor, within 2 seconds",
        "strategy": "latency",
        "quality_floor": 0.7,
        "max_latency_ms": 2000,
    },
    {
        "name": "quality",
        "description": "Best answer first, cost second",
        "strategy": "quality",
        "quality_floor": 0.8,
    },
    {
        "name": "support-bot",
        "description": "Customer support: cheap and fast, with a higher bar for extraction",
        "strategy": "cost",
        "quality_floor": 0.7,
        "task_floors": {"extraction": 0.8},
        "max_cost_usd": "0.01",
    },
    {
        "name": "code-review",
        "description": "Pull request review: coding must score 0.85 or better",
        "strategy": "quality",
        "quality_floor": 0.7,
        "task_floors": {"coding": 0.85},
        "baseline_model": "claude-pro",
    },
)

TEAMS = {
    "Learning Products": "Student-facing AI features",
    "Customer Support": "Inbox automation and agent assist",
    "Platform": "Internal developer tooling",
    "Growth": "Marketing and content",
}

PROJECTS: dict[str, ProjectSpec] = {
    "Alpha Tutor": ProjectSpec(
        "Learning Products",
        "Homework help and explanations for students",
        "balanced",
        520,
        (
            "What is photosynthesis?",
            "Explain why the sky is blue in simple words",
            "Solve 3x + 7 = 22 and show each step",
            "Calculate 15 percent of 240",
            "Summarize the causes of the French Revolution as key points",
            "Translate into French: Where is the train station?",
            "Prove that the square root of 2 is irrational, rigorously",
            "Is this sentence a fact or an opinion? One word. Paris is the best city.",
            "Explain why compound interest grows faster than simple interest, step by step",
            "What is a prime number?",
        ),
    ),
    "Support Copilot": ProjectSpec(
        "Customer Support",
        "Drafts replies and triages the support inbox",
        "support-bot",
        260,
        (
            "Classify this ticket as billing, technical or account. One word. I was charged twice.",
            "How do I request a refund?",
            "How do I reset my password?",
            "Summarize this thread for the next agent: customer cannot log in after the update, "
            "tried clearing cache, still fails on Android",
            "Extract the order id and email as JSON: order A-7741 from ana.lima@example.com",
            "Is this message spam? Yes or no. WIN A FREE CRUISE NOW",
            "Translate into Spanish: Your replacement ships tomorrow",
        ),
    ),
    "Code Review Agent": ProjectSpec(
        "Platform",
        "Reviews pull requests and suggests fixes",
        "code-review",
        90,
        (
            "Review this Python function for bugs:\n```python\ndef add(a, b):\n    return a - b\n```",
            "Refactor this JavaScript to async/await:\n```js\nfetch(url).then(r => r.json())"
            ".then(render)\n```",
            "Find the race condition in this Go handler that writes to a shared map from "
            "concurrent goroutines, and make it production-ready",
            "Write unit tests for the parse_date function in python",
            "Explain the trade-offs of this SQL query plan and optimize the index choice",
        ),
    ),
    "Doc Insights": ProjectSpec(
        "Platform",
        "Summarises contracts and policies, mostly on the local model",
        "cost",
        140,
        (
            "Summarize clause 14 of the services agreement in one sentence",
            "Extract the parties and the effective date as JSON from: This agreement between "
            "Acme Ltd and Globex Inc takes effect on 1 March 2026",
            "Classify this document as contract, invoice or policy. One word. Invoice #4471, "
            "amount due 1,200 USD",
            "Summarize the key points of the data retention policy",
        ),
    ),
    "Marketing Studio": ProjectSpec(
        "Growth",
        "Campaign copy and landing page variants",
        None,
        70,
        (
            "Write three short taglines for a budgeting app",
            "Translate this headline into Spanish: Save more every month",
            "Compare the pros and cons of email versus SMS for retention campaigns",
            "Design a multi-step launch strategy for a new pricing tier, with risks and a plan",
        ),
    ),
}

FEEDBACK_COMMENTS = (
    "Clear and correct",
    "Exactly what the student needed",
    "Too long for a support reply",
    "Missed the second bug",
    "Good summary",
)
