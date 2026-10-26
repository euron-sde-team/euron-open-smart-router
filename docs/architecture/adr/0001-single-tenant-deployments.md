# ADR-0001: One deployment serves one organisation

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

SmartRouter sits between an organisation's applications and its model endpoints, holds that
organisation's endpoint credentials, and ranks models by a quality table measured on that
organisation's benchmark runs. The people who install a self-hosted router are platform teams
routing their own traffic. They need projects, keys and policies inside one organisation, not a
hosting business serving unrelated customers.

Multi-organisation isolation is expensive to get right: every table needs a tenant column, every
query a structural scope, every Redis key a tenant prefix, and the test suite an isolation sweep.
For a router it would also raise questions with no good default: whether two tenants share
health and circuit state for the same provider, and whose benchmark feeds a shared quality table.

## Decision

One deployment serves exactly one organisation. Inside it, attribution works at the level of
teams, projects and gateway keys. Endpoints, models, policies, the quality table and model
health are organisation-wide: one circuit per model, one quality score per (model, task, band).
A project chooses a default policy; a request may select any policy. Dashboard users belong to
the organisation and have one of three roles: owner, admin, viewer.

## Consequences

**Good**
- Every gateway path is simpler and faster: no tenant resolution, no tenant prefix on hot Redis
  keys, and a catalog snapshot that is the whole configuration.
- Health is shared where it should be: a provider outage seen by one project's traffic opens the
  circuit for every project at once.
- Endpoint credentials and the KMS key belong to one organisation, which matches how security
  teams want to reason about them.

**Bad / caveats**
- A provider of SmartRouter as a service would run one deployment per customer. The Terraform
  makes that a variable change, but it costs more than a shared database.
- Policies are not scoped to projects, so a key can select any policy and request any enabled
  model by name. Per-project policy and model allowlists are a contained addition if needed.
- Moving to multi-organisation later means a migration that adds an organisation column and a
  scoped data layer, plus per-tenant health and quality keys.

## Alternatives considered

- **Multi-tenant from day one** (HRMS style). Rejected for 0.1.0: cost without a user.
- **Per-project health and quality.** Rejected: a provider outage is not project specific, and
  splitting benchmark evidence across projects makes every cell smaller and noisier.
