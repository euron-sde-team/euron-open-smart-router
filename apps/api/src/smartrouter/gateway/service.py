"""The routing pipeline: resolve the policy, analyse the request, rank the models, call the
best one, fall back in a controlled way, and log the decision.

Fallback rules (never silent, always recorded):
- only when the policy allows it, and only up to ``max_attempts``;
- only on failures that say nothing about the request itself: not sent (connect error),
  timeout, throttled (429), server error (5xx); a 4xx is the caller's problem and is returned;
- a TokenShield ``402 budget_exceeded`` retries only on *cheaper* candidates, and only when the
  policy enables ``budget_downgrade``;
- for streams, only before the first chunk has reached the client;
- a concrete model name (not ``auto``) is passed through with no routing and no fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi.responses import JSONResponse, Response, StreamingResponse
from redis.exceptions import RedisError

from smartrouter.alerts.service import circuit_opened
from smartrouter.core.container import Container
from smartrouter.core.errors import (
    AppError,
    NotFoundError,
    RateLimitedError,
    UpstreamError,
    UpstreamTimeoutError,
)
from smartrouter.core.money import nusd_to_usd, usd_to_nusd
from smartrouter.core.tracing import tracer
from smartrouter.decisions.recorder import DecisionRecord, DecisionRecorder
from smartrouter.gateway import metrics
from smartrouter.gateway.catalog import Catalog, CatalogModel, KeyInfo, ProjectInfo
from smartrouter.gateway.tokens import count_text
from smartrouter.health.tracker import HealthTracker
from smartrouter.limits.limiter import Limiter
from smartrouter.pricing.engine import cost as price_cost
from smartrouter.providers.adapters.base import (
    ChatResult,
    StreamState,
    UpstreamHTTPError,
    UpstreamNotSentError,
    UpstreamTarget,
    UpstreamUncertainError,
    Usage,
)
from smartrouter.providers.adapters.http import SSE_DONE, sse
from smartrouter.routing.analyzer import Analysis, analyze
from smartrouter.routing.engine import Candidate, ModelView, PolicyView, Ranking, baseline_for, rank

log = logging.getLogger("smartrouter.gateway")
AUTO = "auto"
PASSTHROUGH = PolicyView(
    name="passthrough", strategy="passthrough", allow_fallback=False, max_attempts=1
)
_background: set[asyncio.Task[Any]] = set()


def _spawn(coro: Coroutine[Any, Any, Any]) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


def new_request_id() -> str:
    return "req_" + secrets.token_hex(12)


@dataclass(slots=True)
class RequestContext:
    request_id: str
    project: ProjectInfo | None
    key: KeyInfo | None
    source: str = "api"
    policy_header: str | None = None
    task_override: str | None = None
    complexity_override: int | None = None
    started: float = field(default_factory=time.perf_counter)


@dataclass(slots=True)
class RoutePlan:
    requested_model: str
    policy: PolicyView
    analysis: Analysis
    ranking: Ranking
    baseline: ModelView | None
    routing_us: int
    passthrough: bool = False


@dataclass(slots=True)
class Attempt:
    model: str
    outcome: str  # ok | error | timeout | throttled | budget | skipped
    ms: int
    status: int | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "model": self.model,
                "outcome": self.outcome,
                "ms": self.ms,
                "status": self.status,
                "detail": self.detail,
            }.items()
            if v is not None
        }


class GatewayService:
    def __init__(self, container: Container, catalog: Catalog, *, live_health: bool = True) -> None:
        """``live_health=False`` ranks on catalog latency priors with every circuit closed: the
        benchmark uses it so a run never depends on what the previous run did to health."""
        self.c = container
        self.catalog = catalog
        self.live_health = live_health
        self.health = HealthTracker(container.redis, container.settings.breaker)
        self.recorder = DecisionRecorder(container.db.sessionmaker, container.redis)
        self.limiter = Limiter(container.redis)

    # ── Planning (also used by the simulator, which never calls a model) ────

    def resolve_policy(self, requested: str, ctx: RequestContext) -> PolicyView | None:
        """None means passthrough to a concrete model."""
        name: str | None = None
        if requested == AUTO or requested.startswith(AUTO + ":"):
            name = requested.split(":", 1)[1] if ":" in requested else None
            name = (
                name or ctx.policy_header or (ctx.project.default_policy if ctx.project else None)
            )
            name = name or self.catalog.default_policy
        elif requested in self.catalog.models:
            return None
        else:
            raise NotFoundError(
                f"Model '{requested}' is not configured. Use 'auto' to let SmartRouter choose",
                code="model_not_found",
                details={
                    "model": requested,
                    "routes": ["auto", *(f"auto:{p}" for p in self.catalog.policies)],
                },
            )
        if not name or name not in self.catalog.policies:
            raise NotFoundError(
                f"Routing policy '{name}' does not exist"
                if name
                else "No default routing policy is configured",
                code="policy_not_found",
                details={"policies": sorted(self.catalog.policies)},
            )
        return self.catalog.policies[name]

    async def plan(self, ctx: RequestContext, body: dict[str, Any]) -> RoutePlan:
        t0 = time.perf_counter()
        requested = str(body.get("model") or AUTO)
        policy = self.resolve_policy(requested, ctx)
        analysis = analyze(
            body, task_override=ctx.task_override, complexity_override=ctx.complexity_override
        )
        views = self.catalog.views()
        if policy is None:
            m = self.catalog.models[requested]
            ranking = Ranking(
                (
                    Candidate(
                        requested,
                        True,
                        "requested by name: passed through without routing",
                        cost_nusd=price_cost(
                            m.view.price, analysis.input_tokens, analysis.output_tokens
                        ),
                        latency_ms=float(m.view.latency_prior_ms),
                        quality=0.0,
                        quality_source="n/a",
                    ),
                ),
                floor=0.0,
            )
            return RoutePlan(
                requested,
                PASSTHROUGH,
                analysis,
                ranking,
                m.view,
                int((time.perf_counter() - t0) * 1e6),
                passthrough=True,
            )
        health = await self.health.snapshot([v.alias for v in views]) if self.live_health else {}
        trusted = "complexity" in analysis.overridden

        def quality(alias: str, task: str, band: str) -> tuple[float, str] | None:
            return self.catalog.expected_quality(alias, task, band, trusted=trusted)

        ranking = rank(analysis, policy, views, health, quality)
        baseline = baseline_for(views, policy, analysis)
        return RoutePlan(
            requested, policy, analysis, ranking, baseline, int((time.perf_counter() - t0) * 1e6)
        )

    # ── Serving ─────────────────────────────────────────────────────────────

    async def chat(self, ctx: RequestContext, body: dict[str, Any]) -> Response:
        with tracer.start_as_current_span("router.chat") as span:
            span.set_attribute("smartrouter.request_id", ctx.request_id)
            try:
                plan = await self.plan(ctx, body)
            except AppError as err:
                await self._record_failure(ctx, body, None, err, [])
                raise
            span.set_attribute("smartrouter.policy", plan.policy.name)
            span.set_attribute("smartrouter.task", plan.analysis.task)
            if plan.ranking.selected is None:
                no_model = AppError(
                    f"No model can serve this {plan.analysis.task} request under policy "
                    f"'{plan.policy.name}'",
                    code="no_eligible_model",
                    details={"candidates": [c.as_dict() for c in plan.ranking.candidates]},
                )
                no_model.status_code = 422
                await self._record_failure(ctx, body, plan, no_model, [])
                raise no_model
            concurrency = await self._admit(ctx)
            try:
                if body.get("stream"):
                    return await self._stream(ctx, body, plan, concurrency)
                return await self._call(ctx, body, plan)
            finally:
                if not body.get("stream") and concurrency:
                    await self._release(concurrency)

    async def _admit(self, ctx: RequestContext) -> str | None:
        key = ctx.key
        if key is None:
            return None
        try:
            if key.rpm_limit:
                d = await self.limiter.hit(f"rpm:{key.id}", key.rpm_limit)
                if not d.allowed:
                    raise RateLimitedError(
                        f"Rate limit of {key.rpm_limit} requests per minute reached",
                        code="rpm_limit",
                        headers={"retry-after": str(d.retry_after_s)},
                    )
            if key.concurrency_limit:
                name = f"key:{key.id}"
                if not await self.limiter.acquire(
                    name, key.concurrency_limit, int(self.c.settings.upstream_timeout_s * 2) + 30
                ):
                    raise RateLimitedError(
                        f"Concurrency limit of {key.concurrency_limit} reached",
                        code="concurrency_limit",
                        headers={"retry-after": "1"},
                    )
                return name
        except RedisError:
            return None  # limits are a courtesy here; routing does not hold money
        return None

    async def _release(self, name: str) -> None:
        with contextlib.suppress(RedisError):
            await self.limiter.release(name)

    def _target(self, m: CatalogModel) -> tuple[UpstreamTarget, str]:
        ep = self.catalog.endpoints[m.endpoint_id]
        return (
            UpstreamTarget(
                base_url=ep.base_url,
                api_key=self.catalog.endpoint_keys.get(ep.id),
                upstream_model=m.upstream_model,
                timeout_s=ep.timeout_s or self.c.settings.upstream_timeout_s,
            ),
            ep.kind,
        )

    def _payload(self, body: dict[str, Any], m: CatalogModel) -> dict[str, Any]:
        payload = {**body, "model": m.view.alias}
        payload.pop("stream_options", None)
        ep = self.catalog.endpoints[m.endpoint_id]
        meta = payload.get("metadata")
        if isinstance(meta, dict) and any(k.startswith("benchmark_") for k in meta):
            # Benchmark oracle annotations exist only for simulated endpoints.
            if ep.simulated:
                payload["metadata"] = meta
            else:
                cleaned = {k: v for k, v in meta.items() if not k.startswith("benchmark_")}
                if cleaned:
                    payload["metadata"] = cleaned
                else:
                    payload.pop("metadata", None)
        return payload

    def _order(self, plan: RoutePlan) -> list[Candidate]:
        eligible = plan.ranking.eligible
        limit = plan.policy.max_attempts if plan.policy.allow_fallback else 1
        return eligible[: max(1, limit)]

    async def _health_gate(self, alias: str, *, routed: bool) -> str | None:
        """Re-check health at call time; the ranking's snapshot may be stale. Returns why the
        model must be skipped, or None to call it. A half-open circuit admits one probe; an
        open one admits nothing routed (a pinned model is still called: the caller chose it)."""
        snap = await self.health.snapshot([alias])
        state = snap.get(alias)
        if state and state.state == "half_open":
            return (
                None if await self.health.try_probe(alias) else "half-open: probe already in flight"
            )
        if routed and state and state.state == "open":
            return "circuit opened after ranking"
        return None

    @staticmethod
    def _classify(exc: Exception) -> tuple[str, bool, int]:
        """(outcome, retryable, status) for an upstream failure."""
        if isinstance(exc, UpstreamNotSentError):
            return "error", True, 502
        if isinstance(exc, UpstreamUncertainError):
            return "timeout", True, 504
        if isinstance(exc, UpstreamHTTPError):
            if exc.status == 402 and (
                exc.code == "budget_exceeded" or "x-tokenshield-request-id" in exc.headers
            ):
                return "budget", True, 402
            if exc.status == 429:
                return "throttled", True, 429
            if exc.status >= 500:
                return "error", True, 502
            return "client", False, exc.status
        return "error", True, 502

    async def _call(self, ctx: RequestContext, body: dict[str, Any], plan: RoutePlan) -> Response:
        chain: list[Attempt] = []
        order = self._order(plan)
        budget_ceiling: int | None = None
        last_exc: Exception | None = None
        for cand in order:
            if budget_ceiling is not None and cand.cost_nusd >= budget_ceiling:
                chain.append(
                    Attempt(
                        cand.alias, "skipped", 0, detail="not cheaper than the budget-refused model"
                    )
                )
                continue
            if skip := await self._health_gate(cand.alias, routed=not plan.passthrough):
                chain.append(Attempt(cand.alias, "skipped", 0, detail=skip))
                continue
            m = self.catalog.models[cand.alias]
            target, kind = self._target(m)
            t0 = time.perf_counter()
            try:
                with tracer.start_as_current_span("router.upstream") as span:
                    span.set_attribute("smartrouter.model", cand.alias)
                    result = await self.c.adapters.get(kind).chat(target, self._payload(body, m))
            except (UpstreamNotSentError, UpstreamUncertainError, UpstreamHTTPError) as exc:
                ms = int((time.perf_counter() - t0) * 1000)
                outcome, retryable, status = self._classify(exc)
                if outcome != "client":
                    _spawn(self._health(cand.alias, outcome if outcome != "budget" else "ok", ms))
                chain.append(Attempt(cand.alias, outcome, ms, status, _detail(exc)))
                last_exc = exc
                if outcome == "budget":
                    if not plan.policy.budget_downgrade:
                        break
                    budget_ceiling = cand.cost_nusd
                    continue
                if not retryable or not plan.policy.allow_fallback:
                    break
                continue
            ms = int((time.perf_counter() - t0) * 1000)
            chain.append(Attempt(cand.alias, "ok", ms, 200))
            return await self._success(ctx, body, plan, m, result, chain, ms)
        return await self._exhausted(ctx, body, plan, chain, last_exc)

    async def _success(
        self,
        ctx: RequestContext,
        body: dict[str, Any],
        plan: RoutePlan,
        m: CatalogModel,
        result: ChatResult,
        chain: list[Attempt],
        upstream_ms: int,
    ) -> Response:
        usage = result.usage
        if usage.source == "estimate" or (not usage.input_tokens and not usage.output_tokens):
            content = ((result.body.get("choices") or [{}])[0].get("message") or {}).get("content")
            usage = Usage(
                plan.analysis.input_tokens, 0, count_text(content or "", m.tokenizer), "estimate"
            )
        result.body["model"] = m.view.alias
        rec = self._record(ctx, body, plan, chain, m, usage, result.headers, upstream_ms)
        await self.recorder.record(rec)
        _spawn(self._health(m.view.alias, "ok", upstream_ms))
        self._metrics(rec)
        return JSONResponse(result.body, headers=self._headers(ctx, plan, rec))

    async def _stream(
        self, ctx: RequestContext, body: dict[str, Any], plan: RoutePlan, concurrency: str | None
    ) -> Response:
        chain: list[Attempt] = []
        last_exc: Exception | None = None
        budget_ceiling: int | None = None
        for cand in self._order(plan):
            if budget_ceiling is not None and cand.cost_nusd >= budget_ceiling:
                chain.append(
                    Attempt(
                        cand.alias, "skipped", 0, detail="not cheaper than the budget-refused model"
                    )
                )
                continue
            if skip := await self._health_gate(cand.alias, routed=not plan.passthrough):
                chain.append(Attempt(cand.alias, "skipped", 0, detail=skip))
                continue
            m = self.catalog.models[cand.alias]
            target, kind = self._target(m)
            state = StreamState()
            t0 = time.perf_counter()
            agen = (
                self.c.adapters.get(kind)
                .chat_stream(target, self._payload(body, m), state)
                .__aiter__()
            )
            try:
                first = await agen.__anext__()  # fallback stays possible until this arrives
            except (
                UpstreamNotSentError,
                UpstreamUncertainError,
                UpstreamHTTPError,
                StopAsyncIteration,
            ) as exc:
                ms = int((time.perf_counter() - t0) * 1000)
                if isinstance(exc, StopAsyncIteration):
                    exc = UpstreamUncertainError("empty stream")
                outcome, retryable, status = self._classify(exc)
                if outcome != "client":
                    _spawn(self._health(cand.alias, outcome if outcome != "budget" else "ok", ms))
                chain.append(Attempt(cand.alias, outcome, ms, status, _detail(exc)))
                last_exc = exc
                if outcome == "budget":
                    if not plan.policy.budget_downgrade:
                        break
                    budget_ceiling = cand.cost_nusd
                    continue
                if not retryable or not plan.policy.allow_fallback:
                    break
                continue
            first_ms = int((time.perf_counter() - t0) * 1000)
            chain.append(Attempt(cand.alias, "ok", first_ms, 200))
            pending = self._record(
                ctx, body, plan, chain, m, Usage(source="none"), state.headers, first_ms
            )
            return StreamingResponse(
                self._relay(ctx, body, plan, chain, m, state, agen, first, t0, concurrency),
                media_type="text/event-stream",
                headers=self._headers(ctx, plan, pending),
            )
        if concurrency:
            await self._release(concurrency)
        return await self._exhausted(ctx, body, plan, chain, last_exc)

    async def _relay(
        self,
        ctx: RequestContext,
        body: dict[str, Any],
        plan: RoutePlan,
        chain: list[Attempt],
        m: CatalogModel,
        state: StreamState,
        agen: AsyncIterator[bytes],
        first: bytes,
        t0: float,
        concurrency: str | None,
    ) -> AsyncIterator[bytes]:
        outcome = "pending"
        try:
            yield first
            async for chunk in agen:
                yield chunk
            outcome = "done"
        except (UpstreamNotSentError, UpstreamUncertainError, UpstreamHTTPError) as exc:
            outcome = "broken"
            yield sse(
                {
                    "error": {
                        "code": "upstream_error",
                        "type": "api_error",
                        "message": f"Stream interrupted: {exc}",
                    }
                }
            )
            yield SSE_DONE
        finally:
            finish = asyncio.ensure_future(
                self._finish_stream(ctx, body, plan, chain, m, state, t0, outcome, concurrency)
            )
            _background.add(finish)
            finish.add_done_callback(_background.discard)
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(finish)

    async def _finish_stream(
        self,
        ctx: RequestContext,
        body: dict[str, Any],
        plan: RoutePlan,
        chain: list[Attempt],
        m: CatalogModel,
        state: StreamState,
        t0: float,
        outcome: str,
        concurrency: str | None,
    ) -> None:
        try:
            ms = int((time.perf_counter() - t0) * 1000)
            usage = state.usage or Usage(
                plan.analysis.input_tokens,
                0,
                count_text("".join(state.text), m.tokenizer),
                "estimate",
            )
            rec = self._record(ctx, body, plan, chain, m, usage, state.headers, ms)
            if outcome != "done":
                rec.status, rec.error_code = (
                    "ERROR",
                    "stream_interrupted" if outcome == "broken" else "client_disconnected",
                )
            await self.recorder.record(rec)
            await self._health(m.view.alias, "ok" if outcome != "broken" else "error", ms)
            self._metrics(rec)
        except Exception:
            log.exception("stream accounting failed", extra={"request_id": ctx.request_id})
        finally:
            if concurrency:
                await self._release(concurrency)

    async def _exhausted(
        self,
        ctx: RequestContext,
        body: dict[str, Any],
        plan: RoutePlan,
        chain: list[Attempt],
        last_exc: Exception | None,
    ) -> Response:
        err: AppError
        if isinstance(last_exc, UpstreamHTTPError) and self._classify(last_exc)[0] in (
            "client",
            "budget",
        ):
            body_err = (
                last_exc.body
                if isinstance(last_exc.body, dict) and "error" in last_exc.body
                else None
            )
            err = UpstreamError(last_exc.message, code=(last_exc.code or "upstream_error"))
            err.status_code = last_exc.status
            if body_err:
                err.details = {
                    "upstream": body_err["error"],
                    "attempts": [a.as_dict() for a in chain],
                }
            else:
                err.details = {"attempts": [a.as_dict() for a in chain]}
        elif (
            isinstance(last_exc, UpstreamHTTPError)
            and last_exc.status == 429
            and sum(1 for a in chain if a.outcome != "skipped") == 1
        ):
            # One model, rate limited, nothing else tried (fallback off or a pinned model):
            # the caller should back off, so say 429 and pass the upstream's retry hint on.
            err = RateLimitedError(
                f"{chain[0].model} is rate limiting requests; retry later",
                code="upstream_rate_limited",
                headers={"retry-after": last_exc.headers.get("retry-after", "1")},
                details={"attempts": [a.as_dict() for a in chain]},
            )
        elif isinstance(last_exc, UpstreamUncertainError) and len(chain) == 1:
            err = UpstreamTimeoutError(
                f"{chain[0].model} did not answer in time",
                details={"attempts": [a.as_dict() for a in chain]},
            )
        else:
            tried = ", ".join(a.model for a in chain if a.outcome != "skipped") or "none"
            why = (
                "fallback is disabled by the policy"
                if not plan.policy.allow_fallback
                else "every candidate failed"
            )
            err = UpstreamError(
                f"No model answered ({why}); tried: {tried}",
                code="all_models_failed",
                details={"attempts": [a.as_dict() for a in chain], "policy": plan.policy.name},
            )
        await self._record_failure(ctx, body, plan, err, chain)
        raise err

    # ── Recording ───────────────────────────────────────────────────────────

    async def _health(self, alias: str, outcome: str, ms: int) -> None:
        """Record an upstream outcome; raise one alert per hour when a circuit opens."""
        state = await self.health.record(alias, outcome, ms)
        if state == "open" and outcome != "ok":
            await circuit_opened(self.c.db.sessionmaker, alias, "traffic")

    def _record(
        self,
        ctx: RequestContext,
        body: dict[str, Any],
        plan: RoutePlan,
        chain: list[Attempt],
        m: CatalogModel,
        usage: Usage,
        headers: dict[str, str],
        upstream_ms: int,
    ) -> DecisionRecord:
        computed = price_cost(m.view.price, usage.input_tokens, usage.output_tokens)
        actual, source = computed, ("computed" if usage.source != "none" else "none")
        ts_cost = headers.get("x-tokenshield-cost-usd")
        if ts_cost:
            with contextlib.suppress(InvalidOperation, ValueError):
                actual, source = usd_to_nusd(Decimal(ts_cost)), "tokenshield"
        baseline = plan.baseline
        baseline_cost = (
            price_cost(baseline.price, usage.input_tokens, usage.output_tokens)
            if baseline and usage.source != "none"
            else 0
        )
        selected = plan.ranking.selected
        return DecisionRecord(
            request_id=ctx.request_id,
            requested_model=plan.requested_model,
            strategy=plan.policy.strategy,
            task=plan.analysis.task,
            complexity=plan.analysis.complexity,
            status="SUCCESS",
            project_id=ctx.project.id if ctx.project else None,
            api_key_id=ctx.key.id if ctx.key else None,
            source=ctx.source,
            policy_name=None if plan.passthrough else plan.policy.name,
            signals=list(plan.analysis.signals),
            input_tokens_est=plan.analysis.input_tokens,
            selected_model=selected.alias if selected else None,
            final_model=m.view.alias,
            reason=selected.reason if selected else "",
            attempts=sum(1 for a in chain if a.outcome != "skipped"),
            fallback_chain=[a.as_dict() for a in chain],
            candidates=[c.as_dict() for c in plan.ranking.candidates],
            floor_relaxed=plan.ranking.floor_relaxed,
            estimated_cost_nusd=selected.cost_nusd if selected else 0,
            baseline_model=baseline.alias if baseline else None,
            baseline_cost_nusd=baseline_cost,
            actual_cost_nusd=actual,
            cost_source=source,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=int((time.perf_counter() - ctx.started) * 1000),
            upstream_ms=upstream_ms,
            routing_us=plan.routing_us,
            stream=bool(body.get("stream")),
            upstream_request_id=headers.get("x-tokenshield-request-id")
            or headers.get("x-request-id"),
        )

    async def _record_failure(
        self,
        ctx: RequestContext,
        body: dict[str, Any],
        plan: RoutePlan | None,
        err: AppError,
        chain: list[Attempt],
    ) -> None:
        rec = DecisionRecord(
            request_id=ctx.request_id,
            requested_model=str(body.get("model") or AUTO),
            strategy=plan.policy.strategy if plan else "unknown",
            task=plan.analysis.task if plan else "chat",
            complexity=plan.analysis.complexity if plan else 0,
            status="REJECTED" if not chain else "ERROR",
            project_id=ctx.project.id if ctx.project else None,
            api_key_id=ctx.key.id if ctx.key else None,
            source=ctx.source,
            policy_name=(None if plan.passthrough else plan.policy.name) if plan else None,
            signals=list(plan.analysis.signals) if plan else [],
            input_tokens_est=plan.analysis.input_tokens if plan else 0,
            selected_model=plan.ranking.selected.alias if plan and plan.ranking.selected else None,
            reason=err.message[:500],
            attempts=sum(1 for a in chain if a.outcome != "skipped"),
            fallback_chain=[a.as_dict() for a in chain],
            candidates=[c.as_dict() for c in plan.ranking.candidates] if plan else [],
            error_code=err.code[:40],
            http_status=err.status_code,
            latency_ms=int((time.perf_counter() - ctx.started) * 1000),
            routing_us=plan.routing_us if plan else 0,
            stream=bool(body.get("stream")),
        )
        try:
            await self.recorder.record(rec)
        except Exception:
            log.exception("failed to record a failed decision")
        metrics.DECISIONS.labels(rec.policy_name or "none", "none", rec.task, rec.status).inc()

    def _metrics(self, rec: DecisionRecord) -> None:
        metrics.DECISIONS.labels(
            rec.policy_name or "passthrough", rec.final_model or "none", rec.task, rec.status
        ).inc()
        if rec.attempts > 1:
            metrics.FALLBACKS.labels(rec.policy_name or "passthrough").inc()
        metrics.ROUTING_SECONDS.observe(rec.routing_us / 1e6)
        metrics.SAVINGS.labels(rec.policy_name or "passthrough").inc(
            max(0.0, nusd_to_usd(rec.baseline_cost_nusd - rec.actual_cost_nusd))
        )

    @staticmethod
    def _headers(ctx: RequestContext, plan: RoutePlan, rec: DecisionRecord) -> dict[str, str]:
        h = {
            "x-smartrouter-request-id": ctx.request_id,
            "x-smartrouter-model": rec.final_model or "",
            "x-smartrouter-policy": rec.policy_name or "passthrough",
            "x-smartrouter-strategy": plan.policy.strategy,
            "x-smartrouter-task": plan.analysis.task,
            "x-smartrouter-complexity": str(plan.analysis.complexity),
            "x-smartrouter-attempts": str(max(1, rec.attempts)),
            "x-smartrouter-estimated-cost-usd": f"{nusd_to_usd(rec.estimated_cost_nusd):.9f}",
        }
        if rec.baseline_model:
            h["x-smartrouter-baseline-model"] = rec.baseline_model
        if rec.cost_source != "none":
            h["x-smartrouter-cost-usd"] = f"{nusd_to_usd(rec.actual_cost_nusd):.9f}"
            h["x-smartrouter-baseline-cost-usd"] = f"{nusd_to_usd(rec.baseline_cost_nusd):.9f}"
        fallbacks = [
            a["model"] for a in rec.fallback_chain if a.get("outcome") not in ("ok", "skipped")
        ]
        if fallbacks:
            h["x-smartrouter-fallbacks"] = ",".join(fallbacks)
        h["x-smartrouter-reason"] = rec.reason.encode("ascii", "ignore").decode()[:180]
        return h


def _detail(exc: Exception) -> str:
    if isinstance(exc, UpstreamHTTPError):
        return f"HTTP {exc.status}: {exc.message[:120]}"
    return f"{type(exc).__name__}: {str(exc)[:120]}"
