# Threat model

Scope: the gateway and dashboard API, the worker, Redis, Postgres, the AWS resources the
Terraform creates, and the dashboard. Method: STRIDE per asset, with the mitigation that exists in
this repository and the residual risk. Controls are described in [security.md](security.md).

## Assets

1. **Endpoint credentials** (direct financial exposure at every provider).
2. **Routing integrity**: hard constraints and quality floors hold, an open circuit is never
   chosen, no model is switched silently.
3. **The quality table and benchmark results** (the router ranks by them; tampering steers
   traffic).
4. **Gateway keys and dashboard sessions.**
5. **The decision log and audit log** (savings reports and forensics rely on them).
6. **Prompts and outputs in transit.**
7. **The internal network the api and worker can reach.**

## Threats and mitigations

| # | Threat (STRIDE) | Asset | Mitigation | Residual risk |
| --- | --- | --- | --- | --- |
| T1 | Spoofing: stolen gateway key used from elsewhere | 4, 1 | Keys scoped to one project, peppered hashes, per-key RPM and concurrency limits, expiry, revocation visible cluster-wide in about a second, last-used tracking | A stolen key works until revoked; SmartRouter has no spend limit of its own (use TokenShield) |
| T2 | Spoofing: dashboard credential stuffing | 4 | argon2id, lockout after 5 failures, per-IP throttle, uniform error and timing for unknown emails. The client IP is the direct peer, or the `X-Forwarded-For` entry written by the outermost of `TRUSTED_PROXY_HOPS` trusted proxies (the AWS environments set `1` for the ALB), so a client cannot rotate addresses by forging the header | No second factor in 0.1.0. A `TRUSTED_PROXY_HOPS` set higher than the real number of proxies lets a client choose its address again. The throttle is skipped while Redis is down; the per-account lockout still applies |
| T3 | Tampering: a caller steers routing with headers | 2 | Overrides are limited to policies that exist and to the eight tasks and complexities 1 to 5; every override is recorded as a signal; policy filters still apply | A caller can pick any configured policy, or raise its complexity to raise the floor and reach pricier models; a concrete alias reaches any enabled model (no per-project model allowlist in 0.1.0) |
| T4 | Tampering: an admin repoints an endpoint's `base_url` to a host they control | 1 | Only admins and owners manage endpoints; changing the host of an endpoint that has a stored key is refused (`400 api_key_required`) unless the same request carries a new `api_key`, so the stored credential is never sent to the new host; `provider.updated` audit rows record the change with the new URL; redirects are not followed | An admin can still change the path on the same host. A host whose DNS the admin controls can later be re-pointed without any update to the endpoint (see T5) |
| T5 | Information disclosure / SSRF: an endpoint pointed at an internal address (metadata service, internal admin API) | 7 | `base_url` must be `http` or `https`; on create and update the host is resolved and every address checked: link-local and cloud metadata (`169.254.0.0/16`, `fe80::/10`, `fd00:ec2::254`), unspecified, multicast and reserved addresses are always refused (`endpoint_url_blocked`), loopback and private ones when `ENDPOINT_ALLOW_PRIVATE=false`; no redirects; the endpoint test returns only a status-derived message; on AWS, task egress is limited to HTTPS, PostgreSQL and Redis | Private networks are allowed by default (local and in-VPC models), so an admin can point an endpoint at an internal service; gateway callers receive the JSON body (or up to 500 characters of an error body) it returns to `POST <base>/chat/completions`, and the worker probes `GET <base>/models` every 15 s. The check runs only at create and update: DNS rebinding afterwards (including to a metadata address) is not caught, and endpoints saved before the check, or before `ENDPOINT_ALLOW_PRIVATE` was turned off, are not re-checked. No host allowlist |
| T6 | Information disclosure: benchmark oracle answers sent to a real provider | 3, 6 | Oracle metadata only for endpoints created as `simulated`; the gateway strips `benchmark_*` metadata for every other endpoint | An admin can create a real endpoint with `simulated = true`; the expected answers (from the public suite) then reach that provider and its benchmark scores are meaningless |
| T7 | Tampering: quality table manipulated to steer traffic | 3, 2 | Manual scores and benchmark runs need the admin role; every manual score is audited (`quality.set`); a benchmark run replaces manual cells it measures | An admin can set any score; the decision log shows the quality source ("manual") so the effect is visible |
| T8 | Tampering: retries or redelivery duplicate decisions | 5 | Unique `request_id`, `ON CONFLICT DO NOTHING`, ack after commit, `XAUTOCLAIM` for crashed consumers | None known in normal operation |
| T9 | Tampering: editing decision or audit history | 5 | Append-only triggers on `routing_decisions`, `decision_feedback` and `audit_log` | A database superuser can disable triggers; restrict that role |
| T10 | Tampering: feedback spam skews feedback analytics | 5 | Feedback needs a request id SmartRouter routed (the decision row, or the 24-hour `sr:req:` marker written with it; `404` otherwise); feedback on another project's decision is refused; feedback is rate limited per key (the key's RPM limit, else 600 per minute); feedback is append-only | A key can rate each of its own project's requests many times, within its rate limit. `/v1/models` is not rate limited per key |
| T11 | Information disclosure: endpoint keys read from the API, logs or database | 1 | Write-only API fields, KMS sealing with encryption context (or AES-GCM with associated data), log redaction, decrypted values only in process memory; a rotated key's old plaintext is dropped from the cache when the new one is opened | Memory of a compromised api or worker host |
| T12 | Information disclosure: prompts leaked through the decision log | 6 | Prompts and completions are never stored; decisions keep signals and metadata | Upstream error messages (truncated) are stored and may echo request fragments if a provider includes them |
| T13 | Information disclosure: spend and traffic data via `/metrics` | 5 | ALB restricts `/metrics` to allowed CIDRs | Local deployments expose it on localhost |
| T14 | Denial of service: a failing provider takes the application down | 2 | Circuit breakers shared across replicas, ordered fallback on retryable failures, health probes, single half-open probe; health is re-checked at call time, so a circuit that opened after ranking is skipped; a circuit opened by the worker's probe raises the same alert as one opened by traffic | Fallback after a timeout can bill a request twice; a pinned model is called even when its circuit is open (a lone upstream 429 is returned as `429 upstream_rate_limited` with the provider's `Retry-After`, so clients back off) |
| T15 | Denial of service: flood of requests | 2 | Per-key RPM and concurrency limits, 8 MiB body cap, WAF and per-IP flood guard on AWS | Limits fail open while Redis is down; upstream provider limits still apply |
| T16 | Denial of service: Redis outage | 2 | Routing continues without health awareness; decisions written straight to Postgres; starting a benchmark marks the run failed and answers 503 instead of leaving it queued | Open circuits are forgotten during the outage; the gateway limits and the sign-in throttle are off; `/v1/feedback` from a gateway key fails with 500 |
| T17 | Elevation: viewer performs admin actions | 4 | Role checks in the API on every route; role changes end sessions | None known |
| T18 | Elevation: CSRF against a signed-in admin | 4 | `X-SmartRouter-CSRF` required for cookie mutations, CORS allowlist, `SameSite=Lax` | None known |
| T19 | Spoofing: a stolen session token after sign-out | 4 | Short session lifetime (12 h by default); password, role or access changes end every session | Signing out deletes the cookie but does not revoke the token; a copied token works until it expires |
| T20 | Repudiation: an admin denies changing a policy or endpoint | 5 | Audit row with actor, IP and details written in the same transaction as the change; the IP is taken through `TRUSTED_PROXY_HOPS`, so a forged leftmost `X-Forwarded-For` entry is ignored | A misconfigured `TRUSTED_PROXY_HOPS` records the wrong address; audit rows are visible to admins and owners only |
| T21 | Tampering: shared Redis with TokenShield | 2 | Every SmartRouter key starts with `sr:` and TokenShield's with `ts:`, so a shared Redis database does not mix config stamps or rate-limit windows; the Terraform and Compose still give each product its own Redis | On a shared database, one product filling memory under `noeviction` makes writes fail for both |
| T22 | Supply chain: compromised dependency | all | Locked dependencies (`uv.lock`, `pnpm-lock.yaml`), pip-audit, pnpm audit, CodeQL and gitleaks in the opt-in security workflow | Zero-day in a dependency |

## Out of scope

A compromised host or AWS account, leaked deployment secrets, a malicious owner, and resource
exhaustion of a deployment you operate yourself. See [SECURITY.md](../../SECURITY.md).
