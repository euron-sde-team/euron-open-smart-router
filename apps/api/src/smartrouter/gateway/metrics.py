"""Prometheus metrics for routing. Labels are bounded sets (policies, aliases, tasks)."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

DECISIONS = Counter(
    "smartrouter_decisions_total",
    "Routing decisions by policy, final model, task and outcome",
    ["policy", "model", "task", "status"],
)
FALLBACKS = Counter(
    "smartrouter_fallbacks_total", "Requests that needed more than one attempt", ["policy"]
)
SAVINGS = Counter(
    "smartrouter_savings_usd_total",
    "Cost saved against each request's baseline model (actual tokens at baseline price)",
    ["policy"],
)
ROUTING_SECONDS = Histogram(
    "smartrouter_routing_seconds",
    "Time spent analysing and ranking (excludes the upstream call)",
    buckets=(0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05),
)
