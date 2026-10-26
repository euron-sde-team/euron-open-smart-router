"""Demo data: a realistic organisation, a real benchmark run, 30 days of routing history,
then live traffic through the gateway.

Order matters:
1. Configuration (teams, endpoints, models, policies, projects, keys) through the same services
   the dashboard uses.
2. A real benchmark run against the mock LLM. It fills the quality table the router ranks by.
3. History: every project prompt is planned once by the real routing engine with that quality
   table, so each backfilled decision carries the candidates, scores and reasons the router
   would produce. Outcomes (latency, tokens, rare failures and one outage with fallbacks) are
   sampled around those plans.
4. Live traffic over HTTP, including a simulated outage and a TokenShield budget refusal, so the
   newest rows in the log are genuine.
"""

from __future__ import annotations

import os
import random
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from smartrouter.alerts.models import Alert
from smartrouter.audit.service import Actor
from smartrouter.benchmarks.runner import BenchmarkRunner, create_run
from smartrouter.core.config import get_settings
from smartrouter.core.container import Container
from smartrouter.core.security import hash_password
from smartrouter.decisions.models import DecisionFeedback, RoutingDecision
from smartrouter.demo.data import (
    ENDPOINTS,
    FEEDBACK_COMMENTS,
    MODELS,
    POLICIES,
    PROJECTS,
    TEAMS,
)
from smartrouter.gateway.catalog import Catalog, load_catalog
from smartrouter.gateway.service import GatewayService, RequestContext, RoutePlan
from smartrouter.keys.schemas import KeyCreated, KeyIn
from smartrouter.keys.service import create_key
from smartrouter.pricing.engine import cost
from smartrouter.projects.schemas import ProjectIn, ProjectOut
from smartrouter.projects.service import create_project
from smartrouter.providers.schemas import ModelIn, ProviderIn
from smartrouter.providers.service import create_model, create_provider
from smartrouter.routing.schemas import PolicyIn
from smartrouter.routing.service import create_policy
from smartrouter.teams.schemas import TeamIn
from smartrouter.teams.service import create_team
from smartrouter.users.models import User
from smartrouter.worker import Worker

OWNER_EMAIL = "admin@smartrouter.local"
OUTAGE_MODEL = "gpt-pro"


def _password() -> str:
    return os.environ.get("DEMO_PASSWORD") or "RouterDemo2026"


@dataclass
class Created:
    keys: dict[str, KeyCreated] = field(default_factory=dict)
    projects: dict[str, ProjectOut] = field(default_factory=dict)


async def _owner(container: Container) -> Actor:
    async with container.db.sessionmaker() as session:
        user = await session.scalar(select(User).where(User.email == OWNER_EMAIL))
        if user is None:
            pw = hash_password(_password())
            user = User(email=OWNER_EMAIL, name="Avery Chen", role="owner", password_hash=pw)
            session.add(user)
            for email, name, role in (
                ("ana.lima@example.com", "Ana Lima", "admin"),
                ("sam.okafor@example.com", "Sam Okafor", "viewer"),
            ):
                session.add(User(email=email, name=name, role=role, password_hash=pw))
            await session.commit()
        return Actor(user.id, user.email, "owner", "127.0.0.1")


async def _configure(container: Container, actor: Actor, mock_url: str) -> Created:
    created = Created()
    async with container.db.sessionmaker() as session:
        teams = {
            name: (await create_team(session, TeamIn(name=name, description=desc), actor)).id
            for name, desc in TEAMS.items()
        }
        endpoints: dict[str, uuid.UUID] = {}
        for ep in ENDPOINTS:
            out = await create_provider(
                session,
                ProviderIn(
                    name=ep.name,
                    kind=ep.kind,
                    base_url=f"{mock_url}{ep.path}",
                    api_key=f"sk-demo-{secrets.token_hex(12)}",
                    timeout_s=20,
                    simulated=True,
                ),
                container.sealer,
                actor,
            )
            endpoints[ep.name] = out.id
        for m in MODELS:
            await create_model(
                session,
                ModelIn(
                    alias=m.alias,
                    provider_id=endpoints[m.endpoint],
                    upstream_model=m.upstream,
                    description=m.description,
                    tier=m.tier,
                    context_window=m.context,
                    max_output_tokens=8192,
                    input_per_m=Decimal(m.price[0]),
                    output_per_m=Decimal(m.price[1]),
                    supports_tools=m.tools,
                    latency_prior_ms=m.latency_ms,
                ),
                actor,
            )
        for p in POLICIES:
            await create_policy(session, PolicyIn.model_validate(p), actor)
        for name, spec in PROJECTS.items():
            project = await create_project(
                session,
                ProjectIn(
                    name=name,
                    description=spec.description,
                    team_id=teams[spec.team],
                    default_policy=spec.policy,
                ),
                actor,
            )
            created.projects[name] = project
            created.keys[name] = await create_key(
                session,
                KeyIn(
                    project_id=project.id,
                    name=f"{name} production",
                    rpm_limit=600,
                    concurrency_limit=50,
                ),
                container.settings.api_key_pepper,
                actor,
            )
            if name in ("Alpha Tutor", "Support Copilot"):
                await create_key(
                    session,
                    KeyIn(project_id=project.id, name=f"{name} staging", rpm_limit=60),
                    container.settings.api_key_pepper,
                    actor,
                )
    await container.config_cache.invalidate()
    return created


async def _benchmark(container: Container, actor: Actor) -> dict[str, Any]:
    run_id = await create_run(container, None, None, actor.id)
    summary = await BenchmarkRunner(container).run(run_id, None, None)
    # Phase 2 routed through the gateway; flush those decisions now.
    worker = Worker(container)
    while await worker.drain():
        pass
    return summary


async def _catalog(container: Container) -> Catalog:
    async with container.db.sessionmaker() as session:
        return await load_catalog(session, container.sealer)


async def _plans(
    container: Container, catalog: Catalog, created: Created
) -> dict[str, list[tuple[str, RoutePlan]]]:
    """Plan every project prompt once with the real engine (health is idle, as in a quiet hour)."""
    svc = GatewayService(container, catalog)
    # Warm up (tokenizer load, first Redis connection) so plan.routing_us is the steady state.
    warm = RequestContext(request_id="warm", project=None, key=None)
    for _ in range(3):
        await svc.plan(
            warm, {"model": "auto", "messages": [{"role": "user", "content": "warm up"}]}
        )
    out: dict[str, list[tuple[str, RoutePlan]]] = {}
    for name, spec in PROJECTS.items():
        info = catalog.projects[created.projects[name].id]
        ctx = RequestContext(request_id="plan", project=info, key=None)
        out[name] = [
            (
                prompt,
                await svc.plan(
                    ctx, {"model": "auto", "messages": [{"role": "user", "content": prompt}]}
                ),
            )
            for prompt in spec.prompts
        ]
    return out


def _decision(
    rng: random.Random,
    catalog: Catalog,
    plan: RoutePlan,
    ts: datetime,
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    outage: bool,
) -> dict[str, Any]:
    eligible = plan.ranking.eligible
    selected = plan.ranking.selected
    a = plan.analysis
    base: dict[str, Any] = {
        "request_id": "req_" + secrets.token_hex(12),
        "created_at": ts,
        "project_id": project_id,
        "api_key_id": key_id,
        "source": "api",
        "requested_model": plan.requested_model,
        "policy_name": plan.policy.name,
        "strategy": plan.policy.strategy,
        "task": a.task,
        "complexity": a.complexity,
        "signals": list(a.signals),
        "input_tokens_est": a.input_tokens,
        "selected_model": selected.alias if selected else None,
        "candidates": [c.as_dict() for c in plan.ranking.candidates],
        "floor_relaxed": plan.ranking.floor_relaxed,
        "estimated_cost_nusd": selected.cost_nusd if selected else 0,
        "routing_us": max(
            1, int(plan.routing_us * rng.uniform(0.85, 1.25))
        ),  # measured by the plan
        "stream": rng.random() < 0.3,
        "upstream_request_id": None,
    }
    # Failures: sampled rarely, plus every request that picked the outage model during the outage.
    chain: list[dict[str, Any]] = []
    order = [c.alias for c in eligible][: plan.policy.max_attempts]
    r = rng.random()
    failing = {OUTAGE_MODEL} if outage else set()
    if r < 0.012 and order:
        failing.add(order[0])
    if r < 0.0015:
        failing.update(order)
    final: str | None = None
    for alias in order:
        if alias in failing:
            outcome, status = rng.choice(
                (("error", 502), ("error", 503), ("timeout", None), ("throttled", 429))
            )
            att: dict[str, Any] = {"model": alias, "outcome": outcome, "ms": rng.randint(20, 60)}
            if outcome == "timeout":
                att["ms"] = 20000
            if status:
                att["status"] = status
            att["detail"] = {
                "error": f"upstream returned {status}",
                "throttled": "upstream returned 429",
                "timeout": "no response within 20s",
            }[outcome]
            chain.append(att)
            if not plan.policy.allow_fallback:
                break
            continue
        final = alias
        break
    failed_ms = sum(int(x["ms"]) for x in chain)
    if final is None:
        tried = ", ".join(x["model"] for x in chain) or "none"
        return base | {
            "final_model": None,
            "reason": f"No model answered (every candidate failed); tried: {tried}",
            "attempts": len(chain),
            "fallback_chain": chain,
            "baseline_model": None,
            "baseline_cost_nusd": 0,
            "actual_cost_nusd": 0,
            "cost_source": "none",
            "input_tokens": 0,
            "output_tokens": 0,
            "status": "ERROR",
            "error_code": "all_models_failed",
            "http_status": 502,
            "latency_ms": failed_ms + 3,
            "upstream_ms": failed_ms,
        }
    m = catalog.models[final].view
    in_tok = a.input_tokens
    out_tok = max(4, int(a.output_tokens * rng.uniform(0.25, 0.8)))
    upstream_ms = int(m.latency_prior_ms * rng.lognormvariate(0, 0.3) * (0.7 + out_tok / 1500))
    chain.append({"model": final, "outcome": "ok", "ms": upstream_ms, "status": 200})
    actual = cost(m.price, in_tok, out_tok)
    baseline_cost = cost(plan.baseline.price, in_tok, out_tok) if plan.baseline else 0
    if final == "gpt-mini":
        base["upstream_request_id"] = "req_ts" + secrets.token_hex(6)
    return base | {
        "final_model": final,
        "reason": selected.reason if selected else "",
        "attempts": len(chain),
        "fallback_chain": chain,
        "baseline_model": plan.baseline.alias if plan.baseline else None,
        "baseline_cost_nusd": baseline_cost,
        "actual_cost_nusd": actual,
        "cost_source": "tokenshield" if final == "gpt-mini" else "computed",
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "status": "SUCCESS",
        "error_code": None,
        "http_status": 200,
        "latency_ms": failed_ms + upstream_ms + rng.randint(2, 6),
        "upstream_ms": upstream_ms,
    }


async def _history(
    container: Container,
    catalog: Catalog,
    created: Created,
    plans: dict[str, list[tuple[str, RoutePlan]]],
    days: int,
) -> tuple[int, datetime]:
    rng = random.Random(2026)  # noqa: S311 demo data, not security
    now = datetime.now(UTC)
    start = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    end = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    outage_start = (now - timedelta(days=min(6, max(1, days - 1)))).replace(
        hour=14, minute=10, second=0, microsecond=0
    )
    outage_end = outage_start + timedelta(minutes=85)
    rows: list[dict[str, Any]] = []
    feedback: list[dict[str, Any]] = []
    hour = start
    while hour < end:
        day_index = (hour - start).days
        growth = 0.55 + 0.45 * day_index / max(1, days)
        weekday = hour.weekday() < 5
        diurnal = 0.25 + 0.75 * max(0.0, 1 - abs(hour.hour - 14) / 10)
        for name, spec in PROJECTS.items():
            scale = (
                spec.daily
                / 12
                * growth
                * diurnal
                * (1.0 if weekday or name == "Alpha Tutor" else 0.45)
            )
            n = int(scale) + (1 if rng.random() < scale - int(scale) else 0)
            pid, kid = created.projects[name].id, created.keys[name].id
            for _ in range(n):
                _, plan = rng.choice(plans[name])
                ts = hour + timedelta(seconds=rng.randint(0, 3599))
                row = _decision(rng, catalog, plan, ts, pid, kid, outage_start <= ts < outage_end)
                rows.append(row)
                if row["status"] == "SUCCESS" and rng.random() < 0.03:
                    good = rng.random() < 0.86
                    feedback.append(
                        {
                            "request_id": row["request_id"],
                            "score": 1 if good else -1,
                            "comment": rng.choice(
                                FEEDBACK_COMMENTS[:2] if good else FEEDBACK_COMMENTS[2:4]
                            )
                            if rng.random() < 0.3
                            else None,
                            "created_at": ts + timedelta(minutes=rng.randint(1, 30)),
                        }
                    )
        hour += timedelta(hours=1)
    async with container.db.sessionmaker() as session, session.begin():
        for i in range(0, len(rows), 1000):
            await session.execute(insert(RoutingDecision), rows[i : i + 1000])
        for i in range(0, len(feedback), 1000):
            await session.execute(insert(DecisionFeedback), feedback[i : i + 1000])
        await session.execute(
            insert(Alert)
            .values(
                kind="health",
                severity="critical",
                message=f"Circuit opened for {OUTAGE_MODEL}: 5 consecutive upstream failures; "
                "traffic fell back to the next eligible model",
                dedupe_key=f"seed:circuit:{OUTAGE_MODEL}",
                created_at=outage_start + timedelta(minutes=1),
            )
            .on_conflict_do_nothing()
        )
    return len(rows), outage_start


async def _traffic(api_url: str, mock_url: str, created: Created, n: int) -> dict[str, int]:
    """Live requests through the real gateway: routed, streamed, pinned, failed over, refused."""
    keys = {name: k.key for name, k in created.keys.items()}
    stats: dict[str, int] = {}
    rng = random.Random(7)  # noqa: S311 demo data, not security
    async with (
        httpx.AsyncClient(base_url=api_url, timeout=60) as client,
        httpx.AsyncClient(base_url=mock_url, timeout=10) as mock,
    ):

        async def send(project: str, prompt: str, **extra: Any) -> None:
            headers = {"authorization": f"Bearer {keys[project]}", **extra.pop("headers", {})}
            body = {"model": "auto", "messages": [{"role": "user", "content": prompt}], **extra}
            r = await client.post("/v1/chat/completions", json=body, headers=headers)
            label = f"{r.status_code}:{r.headers.get('x-smartrouter-model', '-')}"
            stats[label] = stats.get(label, 0) + 1

        names = list(PROJECTS)
        for i in range(n):
            project = names[i % len(names)]
            prompt = rng.choice(PROJECTS[project].prompts)
            await send(project, prompt, stream=i % 5 == 0)
        # A team pinning a concrete model: passthrough, no routing.
        await send("Marketing Studio", "Write a tagline for a running shoe", model="gpt-fast")
        # Outage: gpt-pro fails, the circuit opens and requests fall back.
        await mock.post("/admin/faults", json={"model": "mock-pro", "mode": "error", "count": 8})
        for _ in range(6):
            await send(
                "Alpha Tutor",
                "Explain why compound interest grows faster than simple interest, step by step",
                headers={"x-smartrouter-policy": "quality", "x-smartrouter-complexity": "5"},
            )
        await mock.delete("/admin/faults")
        # TokenShield refuses on budget. The cost policy may downgrade only to a cheaper model that
        # still clears its quality floor; none does here, so the 402 reaches the client.
        await mock.post("/admin/faults", json={"model": "mock-mini", "mode": "budget", "count": 2})
        for _ in range(2):
            await send(
                "Support Copilot",
                "How do I reset my password?",
                headers={"x-smartrouter-policy": "cost"},
            )
        await mock.delete("/admin/faults")
    return stats


async def run(days: int = 60, traffic: int = 120, api_url: str = "http://localhost:4230") -> None:
    settings = get_settings()
    if settings.environment == "production":
        raise SystemExit(
            "seed-demo creates well-known demo credentials and refuses to run in production"
        )
    container = Container.build(settings)
    try:
        async with container.db.sessionmaker() as session:
            if await session.scalar(select(func.count()).select_from(RoutingDecision)):
                print("demo data already present; skipping (make down to reset)")
                return
        mock_url = os.environ.get("MOCK_LLM_URL", "http://mock-llm:4250")
        actor = await _owner(container)
        created = await _configure(container, actor, mock_url)
        summary = await _benchmark(container, actor)
        catalog = await _catalog(container)
        plans = await _plans(container, catalog, created)
        rows, outage = await _history(container, catalog, created, plans, days)
        await Worker(container).rollups()
        async with container.db.sessionmaker() as session, session.begin():
            await session.execute(text("ANALYZE routing_decisions"))
        print(f"benchmark: baseline {summary['baseline']}, {summary['cases']} cases")
        for name, p in summary["policies"].items():
            print(
                f"  {name:<12} quality {p['quality']:.2f}  saves {p.get('savings_pct', 0):>5}%"
                f"  quality change {p.get('quality_delta', 0):+.2f}"
            )
        print(f"seeded configuration and {rows} historical decisions (outage {outage:%d %b %H:%M})")
        if traffic:
            stats = await _traffic(api_url, mock_url, created, traffic)
            print("live traffic:", ", ".join(f"{k}={v}" for k, v in sorted(stats.items())))
        public_url = os.environ.get("SMARTROUTER_PUBLIC_URL", api_url)
        print()
        print(f"  dashboard login   {OWNER_EMAIL} / {_password()}")
        print(f"  gateway key       {created.keys['Alpha Tutor'].key}  (Alpha Tutor)")
        print("  try it:")
        print(f"    curl -i {public_url}/v1/chat/completions -H 'authorization: Bearer <key>' \\")
        print("      -H 'content-type: application/json' \\")
        print(
            '      -d \'{"model":"auto","messages":[{"role":"user","content":"Solve 3x + 7 = 22"}]}\''
        )
    finally:
        await container.close()
