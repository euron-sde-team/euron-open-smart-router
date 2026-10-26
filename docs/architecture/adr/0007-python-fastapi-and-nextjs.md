# ADR-0007: Python and FastAPI for the gateway, Next.js for the dashboard

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

The router lives in the LLM ecosystem: tokenizers (tiktoken) for size estimates, provider wire
formats, and evaluation tooling for the benchmark are Python first. The Euron open-source projects
share a standard: Python and FastAPI backends, Next.js frontends. SmartRouter is also a companion
to TokenShield, which made the same choice, so code and operational knowledge carry over.

## Decision

The API, gateway, worker, benchmark runner and mock LLM are Python 3.12 on FastAPI, Pydantic v2,
SQLAlchemy 2 (async, asyncpg), Alembic, redis-py and httpx, managed by uv and checked with ruff and
mypy `--strict`. The dashboard is Next.js 15 (App Router, React 19, TypeScript strict, Tailwind,
shadcn components, TanStack Query), proxying `/api` and `/v1` to the API so the browser stays
same-origin.

## Consequences

**Good**
- tiktoken and the provider wire formats are first-class; the benchmark graders are a few lines
  of standard library code.
- Async I/O end to end on the request path; blocking SDKs (boto3) run in threads.
- Typed contracts both sides: the dashboard's types are generated from the OpenAPI document, which
  a test keeps in sync with the app (`tests/unit/test_openapi_drift.py`).

**Bad / caveats**
- Python's per-request CPU cost is higher than Go or Rust. The routing step is bounded (a fixed set
  of regular expressions and a linear scan of the models), its time is recorded on every decision
  (`routing_us`), and the API scales horizontally.

## Alternatives considered

- **Go.** Faster per request, weaker ecosystem fit for tokenizers and providers.
- **A plain React SPA.** Next.js gives routing, code splitting and a same-origin proxy.
