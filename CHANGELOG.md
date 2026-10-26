# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-26

First public release: SmartRouter 0.1.0.

### Added

- OpenAI-compatible gateway (`POST /v1/chat/completions` with streaming, `GET /v1/models`,
  `POST /v1/feedback`). Clients send `model="auto"`, `auto:<policy>` or a concrete model;
  concrete models pass through unrouted and are never switched.
- Deterministic, explainable request analysis: task (8 kinds), complexity 1 to 5 with the
  signals that drove it, input size and required capabilities (tools, vision, JSON).
  Callers can override task and complexity with headers.
- Routing policies: cost, latency, quality and balanced strategies or custom weights, a
  quality floor with a hard-request boost and per-task floors, latency and cost ceilings,
  allowed and excluded models, fallback limits, a baseline model for savings, and a choice
  between relaxing the floor and rejecting when nothing qualifies.
- Ranking engine: hard filters, then cost (log-scaled), latency and expected quality, with a
  score and a reason for every candidate. Quality is shrunk hierarchically from benchmark
  cells and averaged over both difficulty bands, weighted by how often the analyzer
  misjudges difficulty.
- Health tracking in Redis shared by every replica: latency percentiles, error rates and a
  circuit breaker per model (closed, open, half-open with a single probe), plus endpoint
  probes from the worker. Retryable failures fall back to the next eligible model.
- TokenShield integration: an endpoint kind that reads TokenShield's actual cost and honours
  its `402 budget_exceeded`, downgrading to a cheaper model when the policy allows. No second
  budget ledger.
- Append-only decision log through a Redis Stream, hourly rollups, savings against the
  baseline, and analytics by model, task, policy, project and status.
- Reproducible benchmark: 160 graded cases across 8 tasks and 5 difficulties, run on every
  model and then through every policy with 2-fold cross-fitting; results feed the quality
  table, and every graded answer is stored as a gzipped JSON lines file in object storage.
- Dashboard: overview, decision log with a "why this model" drawer, models (catalog, live
  health, quality matrix), benchmarks, route simulator, policies, projects, API keys,
  endpoints, teams, users, audit log and settings; light and dark themes.
- A deterministic mock LLM with per-task skill, latency profiles, fault injection and a
  TokenShield-emulating route, for the demo and the test suites.
- Terraform for AWS (VPC, RDS, ElastiCache, S3, KMS, Secrets Manager, ECR, ECS Fargate
  behind an ALB, CloudWatch with a circuit-openings alarm), applied locally against Floci.
