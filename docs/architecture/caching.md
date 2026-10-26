# Caching

SmartRouter caches configuration, not answers. Every routed request calls a model; what is
cached is what the gateway needs to decide which one (the catalog, key records, opened
credentials) and what the dashboard reads repeatedly (analytics). Model health is deliberately
not cached: it is the input that must be fresh. The summary table is in
[HLD.md](HLD.md#caching-layers); this document is the key design, the invalidation rules and
what is tested.

- [What is cached](#what-is-cached)
- [The config cache](#the-config-cache)
- [Opened endpoint keys](#opened-endpoint-keys)
- [Analytics](#analytics)
- [Rollups](#rollups)
- [The dashboard](#the-dashboard)
- [What is not cached](#what-is-not-cached)
- [Invalidation reference](#invalidation-reference)
- [What is tested](#what-is-tested)

## What is cached

| Layer | What | Where | Key | TTL | Invalidation |
| ----- | ---- | ----- | --- | --- | ------------ |
| Config cache | Catalog snapshot, API key records | In-process, per api or worker process | `catalog`, `key:<prefix>` | 5 s | Admin write: local clear + `INCR sr:cfg:version`; others notice within 1 s |
| Opened endpoint keys | Decrypted provider credentials | In-process module dict | `(provider_id, sha256(sealed_key))` | Process lifetime | New ciphertext, new key; the old plaintext for that endpoint is dropped when the new one is opened |
| Derived quality | Band scores, task offsets | Inside each `Catalog` | (model, band), (model, task) | Catalog lifetime | Rebuilt with the catalog |
| Analytics | Overview, time series, breakdown | Redis | `sr:an:ov:<range>:<project>`, `sr:an:ts:<range>:<project>`, `sr:an:bd:<range>:<by>:<project>` | 10 s | Expiry only |
| Rollups | Hourly decision aggregates | Postgres `decision_rollups_hourly` | hour + dimensions | Recomputed each minute | Idempotent recompute |
| Dashboard | API responses | TanStack Query | `apps/web/src/lib/query-keys.ts` | 5 s to 5 min by kind | Mutation invalidates by key |

## The config cache

`core/cache.py ConfigCache` is an in-process dictionary of `key -> (expires_at, value)` with
three properties:

- **Short TTL.** Entries live 5 s, so even with no invalidation signal a process never serves
  configuration more than 5 s old.
- **Single flight.** A miss takes a per-key `asyncio.Lock`; concurrent requests for the same key
  wait for one load and share it, so an expiry under load causes one catalog query per process,
  not one per request.
- **Cluster-wide invalidation.** `invalidate()` clears the local dictionary and runs
  `INCR sr:cfg:version`. On every `get`, at most once per second, each process reads the stamp
  with `GET`; if it differs from the last one seen, the process clears its dictionary. A revoked
  key or a changed policy is therefore visible on every replica within about a second.

If Redis is unreachable, the stamp cannot be read or bumped: processes fall back to TTL expiry
alone (a warning is logged when a bump fails), so changes take up to 5 s.

The two kinds of entries:

| Key | Value | Loaded by |
| --- | ----- | --------- |
| `catalog` | The whole routing snapshot: enabled endpoints and models, enabled policies and the default, non-archived projects, every `quality_scores` row, the calibration from the latest finished benchmark run, opened endpoint keys | `gateway/catalog.py load_catalog` (a handful of queries) |
| `key:<prefix>` | One gateway key record (`KeyInfo`: hash, project, limits, expiry, revoked), or `None` for an unknown prefix | `gateway/catalog.py load_key` (one query by unique prefix) |

Caching `None` for an unknown prefix means a burst of requests with a bad key costs one query
per process per 5 s, not one per request. A newly created key has a fresh random prefix, so it
is never shadowed by a cached `None`.

The worker loads the catalog directly (health probe, benchmark runs) without the cache, so it
always sees the current configuration.

## Opened endpoint keys

The catalog carries each endpoint's decrypted key so the gateway never calls KMS on the request
path. `_open_key` memoises the plaintext in a module-level dictionary keyed by the endpoint id and
the SHA-256 of the sealed bytes. A catalog reload after an unrelated change finds every key
already open. Rotating a key writes a new ciphertext (AES-GCM uses a fresh nonce; KMS returns a new
blob), so its digest changes and the next catalog load opens it once. Old entries are not evicted:
the dictionary grows by one entry per rotation for the life of the process.

## Analytics

`decisions/analytics.py Analytics._cached` wraps the three dashboard queries:

- **Key.** `sr:an:` plus the query kind (`ov`, `ts`, `bd`), the range (`1h`, `24h`, `7d`, `30d`,
  `90d`), the breakdown dimension for `bd`, and the project filter (or `None`).
- **Value.** The response serialised with orjson.
- **TTL.** 10 s (`CACHE_TTL_S`). There is no explicit invalidation: the dashboard polls (the
  overview every 30 s), and a new decision appearing up to 10 s late is acceptable for charts.
- **Failure.** A Redis error on read or write is swallowed and the query runs against Postgres.

The queries themselves are cheap because they read rollups for closed hours and the raw decision
log only after the last rolled hour.

## Rollups

`decision_rollups_hourly` is a pre-aggregated cache of `routing_decisions`, maintained by the
worker's leased `rollups` job every 60 s. Each run recomputes every closed hour from two hours
before the latest rolled hour (or from the first decision) up to the open hour, with
`INSERT ... ON CONFLICT DO UPDATE` that overwrites the hour's aggregates. Recomputing is
idempotent, so late decisions drained into a recent hour are picked up on the next run. Analytics
read raw rows from the hour after the latest rolled hour onward, so a lagging or stopped worker
never leaves a gap, only a slower query. Benchmark decisions are excluded from both.

## The dashboard

The web app holds server state in TanStack Query. Query keys are centralised in
`apps/web/src/lib/query-keys.ts`, so a mutation invalidates exactly what it touched (for example
`["policies"]` after a policy edit). `staleTime` is chosen per kind of data (`STALE`):

| Kind | `staleTime` | Used for | Polling |
| ---- | ----------- | -------- | ------- |
| `analytics` | 10 s | Overview, time series, breakdowns, alerts | Overview 30 s, alerts 30 s |
| `config` | 30 s | Projects, keys, endpoints, models, quality table, policies, users, teams, audit, system, benchmark runs | Benchmark runs every 3 s while one is queued or running |
| `live` | 5 s | Model health, the decision log | Health 10 s; decision log 10 s when auto-refresh is on |
| `static` | 5 min | The signed-in user, a decision's detail, a benchmark run's detail, the benchmark suite | none |

The client default is 30 s for anything not listed. The analytics `staleTime` matches the API's
10 s Redis TTL, so the browser does not refetch data the server would answer from cache anyway.

## What is not cached

- **Completions.** SmartRouter has no response cache. Put TokenShield behind it for exact and
  semantic caching ([ADR-0008](adr/0008-tokenshield-integration-without-a-second-ledger.md)).
- **Model health.** Read from Redis on every routed request in one pipeline, because a stale
  view would send traffic to a model whose circuit just opened.
- **HTTP responses.** The API sends no `Cache-Control` or `ETag` headers; dashboard data goes
  through React Query instead. Responses over 2 KiB are gzipped.

## Invalidation reference

| Change | Calls `invalidate()` | Visible on every api process |
| ------ | -------------------- | ---------------------------- |
| Endpoint created or updated (including key rotation, enable or disable) | yes | About 1 s |
| Model created or updated | yes | About 1 s |
| Policy created, updated or deleted | yes | About 1 s |
| Manual quality score | yes | About 1 s |
| Project created or updated (including archive, default policy) | yes | About 1 s |
| Team deleted | yes | About 1 s |
| Key updated or revoked | yes | About 1 s |
| Key created | no (a new prefix is never cached) | Immediately |
| Benchmark phase 1 written to `quality_scores` | yes | About 1 s |
| Benchmark run finished (new calibration in the summary) | no | Within the 5 s TTL |
| Circuit reset | no (health is not cached) | Immediately |
| Any of the above with Redis down | local process only | Others within 5 s |

`invalidate()` always clears the whole local dictionary and bumps one stamp, so there is no
per-key invalidation to get wrong.

## What is tested

Invalidation is exercised end to end by integration tests that change configuration through the
admin API and immediately send gateway traffic: `test_revoked_and_expired_keys_are_refused` (a
revoked key fails on the next request) and `test_disabling_a_model_removes_it_from_routing`. Both
run in a single process, so they prove the local clear. `test_admin_writes_invalidate_the_catalog_cluster_wide`
also asserts that an admin write moves the `sr:cfg:version` stamp every other process compares,
and that routing changes after the one-second check interval; it too runs in one process, so the
other side of the stamp check is not exercised by a second instance. There is no dedicated unit
test of `ConfigCache` (TTL expiry, single flight, stamp comparison) in 0.1.0.
