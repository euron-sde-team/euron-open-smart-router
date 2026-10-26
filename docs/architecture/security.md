# Security

SmartRouter holds the keys to an organisation's model endpoints, decides which of them receives
each request, and keeps a log of every decision. This document covers how it protects those
keys, who can do what, what it stores, and what an operator must configure. The threat analysis
and the residual risks are in [threat-model.md](threat-model.md).

## Gateway keys

- Format `tsr_<prefix>_<secret>`: a 12-hex-character prefix (the lookup key) and a 32-byte
  URL-safe random secret.
- Stored as **HMAC-SHA256(secret, API_KEY_PEPPER)**. A leaked database alone cannot be turned
  into working keys; the pepper lives in Secrets Manager, not in the database. Comparison is
  constant time.
- Lookup is by the unique prefix, one indexed query, cached per prefix for 5 s
  ([caching.md](caching.md#the-config-cache)), so authentication never scans keys.
- The full key is shown once, at creation. Listing endpoints return `tsr_<prefix>_****`.
- Keys belong to one project, carry their own RPM and concurrency limits and an optional
  expiry. Revocation takes effect cluster-wide within about a second (config version stamp). A
  key whose project is archived is refused with `403 project_unavailable`.

## Endpoint credentials

- Written once through the API, never returned. Responses expose only `has_key` and a hint (the
  last four characters).
- Sealed before storage. With `KEY_MODE=kms` the credential is encrypted by AWS KMS (Floci
  locally) with the encryption context `smartrouter:record = provider:<id>`, so a ciphertext
  copied onto another endpoint row fails to decrypt. The `service` module's task role allows
  `kms:Encrypt` and `kms:Decrypt` only with that encryption context key. With `KEY_MODE=local`,
  AES-256-GCM with a key derived (SHA-256) from `LOCAL_MASTER_KEY`, a random 12-byte nonce, and
  the same context as associated data.
- Decrypted values are cached in process memory per ciphertext digest, so KMS is called only
  when a credential changes. When a rotated key is opened, the old plaintext for that endpoint
  is dropped from the cache rather than kept beside the new one.
- The credential is sent only to the endpoint's own `base_url`: as `Authorization: Bearer` for
  OpenAI-compatible and TokenShield endpoints, as `x-api-key` for Anthropic. The HTTP client does
  not follow redirects, so a 3xx cannot carry the key to another host.
- **Moving an endpoint needs its key again.** An update that changes the host (and port) of
  `base_url` on an endpoint with a stored key is refused with `400 api_key_required` unless the
  same request carries `api_key`. An admin who can edit endpoints therefore cannot send a stored
  credential to a host they control without already knowing it. A change of path on the same
  host is allowed.

## Dashboard authentication

- Passwords hashed with argon2id. Policy: at least 10 characters with upper and lower case
  letters and a digit.
- Unknown emails take the same time as wrong passwords (a dummy hash is verified) and get the
  same error.
- Five consecutive failures lock an account for 15 minutes; sign-in is also throttled to 20
  attempts per minute per client IP (the per-IP throttle is skipped while Redis is down; the
  lockout, kept in Postgres, still applies).
- Sessions are HS256 JWTs (`SESSION_SECRET`, audience `smartrouter-dashboard`,
  `SESSION_TTL_HOURS`, default 12), carried in an `httpOnly`, `SameSite=Lax` cookie `sr_session`
  (`Secure` when `COOKIE_SECURE=true`) or as a bearer token for API clients.
- Every session carries the user's `session_version`, and every request re-reads the user.
  Changing a password, changing a role or disabling an account bumps the version, which ends all
  existing sessions immediately. Signing out deletes the cookie; it does not revoke the token
  itself.
- **CSRF:** cookie-authenticated mutations must send `X-SmartRouter-CSRF: 1`. Browsers cannot
  add a custom header to a cross-site request without a CORS preflight, which the allowlist
  refuses. Bearer-authenticated requests need no such header, because browsers never attach
  Authorization headers cross-site.

## Authorisation

| Role | Can |
| --- | --- |
| viewer | Read the decision log, analytics, policies, the quality table, model health, endpoints, models, projects, teams, keys (prefixes only), benchmarks and alerts; run the route simulator |
| admin | Everything a viewer can, plus manage endpoints (including testing them), models, policies, manual quality scores, projects, teams and keys; queue benchmarks; reset circuits; acknowledge alerts; list users; read the audit log |
| owner | Everything, plus invite users, change roles and disable accounts |

Roles are enforced by the API on every route (the dashboard only hides controls). Guards keep at
least one active owner, prevent changing your own role or disabling yourself, and let only
owners manage owners.

## Gateway callers

A gateway key authorises routing within its project, under the policy the request selects:

- The caller chooses among configured policies (`auto:<name>` or `x-smartrouter-policy`) and may
  override the task and complexity with `x-smartrouter-task` and `x-smartrouter-complexity`.
  Policies are organisation-wide, so the hard constraints a caller cannot bypass are the ones in
  the policy it selects: allowed and excluded models, cost and latency ceilings, floors.
- A concrete model alias is passed through to that model. Any enabled model can be requested by
  name; there is no per-project or per-key model allowlist in 0.1.0.
- `POST /v1/feedback` accepts only a request id SmartRouter routed (`404` otherwise: the
  decision row, or the 24-hour `sr:req:<request_id>` marker written with it), refuses feedback
  on a decision that belongs to another project, and is rate limited per key (the key's RPM
  limit, else 600 per minute). A key cannot fill the feedback table with rows for invented
  request ids.

## Benchmark oracle isolation

Benchmark cases can carry oracle metadata (the expected answer) so the mock LLM can simulate
skill. It must never reach a real provider:

- Endpoints are marked `simulated` at creation; the flag cannot be changed later.
- Phase 1 of a run attaches the metadata only for simulated endpoints, and the gateway strips
  every `benchmark_*` metadata key before calling any endpoint that is not simulated
  (`GatewayService._payload`).

See [benchmarks.md](benchmarks.md#oracle-metadata-and-simulated-endpoints).

## Data handling

- **Prompts and completions are not stored.** A decision row keeps the analyzer's signals (rule
  names and matched words from a fixed vocabulary, such as "hard cues: concurrency, edge
  cases"), token counts, the candidates and their scores, the attempt chain, costs and timing.
  On a failure it also keeps the upstream error message, truncated to 120 characters per attempt
  and 500 for the reason; a provider that echoes request content in its error messages can put a
  fragment of it there.
- Benchmark per-case files hold the first 400 characters of each model's answer to the public
  suite's prompts, not user traffic.
- `routing_decisions`, `decision_feedback` and `audit_log` are append-only (database triggers).
  Every dashboard mutation writes an audit row in the same transaction, including endpoint key
  rotations (`provider.key_rotated`), circuit resets and queued benchmarks.
- JSON logs redact `authorization`, `api_key`, `password`, `secret`, `token` and `x-api-key`
  fields, whatever a caller puts in them.

## Outbound requests and endpoint URLs

The gateway, the worker's health probe and the endpoint test all make HTTP requests to each
endpoint's `base_url`, with the sealed credential attached. Admins set that URL. What exists:

- `base_url` must parse as an `http` or `https` URL (Pydantic `HttpUrl`).
- **Address checks on create and update** (`providers/urls.py`). The host is resolved, and
  every address it resolves to is checked; one bad address refuses the URL with
  `400 endpoint_url_blocked`, as does a host that does not resolve:

  | Address | Refused |
  | --- | --- |
  | Link-local and cloud metadata: `169.254.0.0/16` (includes `169.254.169.254`, the AWS, GCP and Azure metadata service), `fe80::/10`, `fd00:ec2::254` (AWS IPv6 metadata) | Always |
  | Unspecified (`0.0.0.0`, `::`), multicast, reserved | Always |
  | Loopback (`127.0.0.0/8`, `::1`) and private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`, and the other ranges Python counts as private) | Only with `ENDPOINT_ALLOW_PRIVATE=false` |

  Private addresses are **allowed by default**, because local models (Ollama on the same host,
  vLLM inside the VPC) are a core use. An admin can therefore still point an endpoint at an
  internal service on a private network, and gateway callers then receive whatever JSON it
  returns to a `POST .../chat/completions`, or up to 500 characters of its error body. Set
  `ENDPOINT_ALLOW_PRIVATE=false` when every endpoint is a public provider.
- Redirects are never followed.
- The endpoint test returns only reachability, latency and a status-derived message, never the
  response body.

What the check does not cover:

- **DNS rebinding.** The host is resolved at create and update time only. The gateway, the
  probe and the endpoint test resolve it again on every connection and do not re-check, so a
  host whose DNS later answers with a blocked address (including a metadata address) is
  reached. Pin endpoints to hosts you control or trust.
- **Rows written before the check existed**, or before `ENDPOINT_ALLOW_PRIVATE` was turned
  off, are not re-checked until they are next saved.
- There is no allowlist of hosts and no requirement for `https`.

These are listed as residual risks in [threat-model.md](threat-model.md). On AWS the task
security groups allow egress on HTTPS, PostgreSQL and Redis only, which narrows but does not
close them.

## Rate limits and abuse controls

- Per gateway key: RPM (sliding window) and concurrency, atomic in Redis. They fail open when
  Redis is down, because routing holds no money; put TokenShield in front of the providers for
  spend limits.
- Per gateway key on `POST /v1/feedback`: the key's RPM limit, else 600 per minute. Unlike the
  gateway limits it does not fail open.
- Per client IP on sign-in: 20 per minute; per account: lockout after 5 failures. The client IP
  is the direct peer unless `TRUSTED_PROXY_HOPS` says how many proxies in front of the API
  append `X-Forwarded-For`; then it is the Nth entry from the right, the one the outermost
  trusted proxy wrote. Entries further left come from the client and are ignored, so a forged
  `X-Forwarded-For` cannot dodge the throttle or put a fake address in the audit log. Uvicorn's
  own `proxy_headers` handling is off, so nothing else trusts the header.
- Request bodies are capped at 8 MiB on the gateway.
- On AWS, a WAF with managed rules and a per-IP flood guard sits in front; rules that inspect
  request bodies run in count mode, because prompts routinely contain code.

## HTTP hardening

- Security headers on every API response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Opener-Policy: same-origin`, a restrictive `Permissions-Policy`. The dashboard
  sets its own equivalents.
- CORS allowlist from `CORS_ORIGINS`; credentials allowed only for those origins.
- Errors never include stack traces or internal detail; unhandled exceptions return a generic 500
  and are logged with the request id.
- On AWS, the ALB serves `/metrics` only to `metrics_allowed_cidrs`, and redirects HTTP to HTTPS
  with a TLS 1.3 policy.

## Production boot guards

With `ENVIRONMENT=production`, `Settings.guard()` refuses to start unless:

- `SESSION_SECRET` and `API_KEY_PEPPER` are at least 32 characters, are not a known placeholder,
  do not contain the development marker `not-for-production`, and differ from each other,
- `LOCAL_MASTER_KEY` is at least 32 characters and not the development default when
  `KEY_MODE=local`,
- `AWS_ENDPOINT_URL` is unset when `SMARTROUTER_CLOUD=aws`.

The demo seed refuses to run in production.

## Operator checklist

- [ ] Generate `SESSION_SECRET` and `API_KEY_PEPPER` with a CSPRNG (the Terraform `secrets` module
      does) and keep them in Secrets Manager.
- [ ] Use `KEY_MODE=kms` with a dedicated key; the `service` module scopes `kms:Decrypt` to the
      task roles and the `smartrouter:record` encryption context.
- [ ] Set `COOKIE_SECURE=true` and serve the dashboard over HTTPS only.
- [ ] Set `CORS_ORIGINS` to the dashboard's exact origin.
- [ ] Use `https` base URLs for every hosted endpoint, review endpoint changes in the audit log,
      and keep endpoint management to admins you trust with the provider keys.
- [ ] Never mark a real provider endpoint as `simulated`.
- [ ] Run Redis with AOF persistence and `noeviction`, TLS and an auth token (the `cache` module
      does). Every SmartRouter key starts with `sr:`, so sharing a database with TokenShield
      (`ts:`) is safe, but a dedicated one keeps `noeviction` and memory alarms per product.
- [ ] Set `TRUSTED_PROXY_HOPS` to the number of proxies in front of the API that append
      `X-Forwarded-For` (the AWS environments set `1` for the ALB). Too high a value lets a
      client choose its own address; `0` (the default) uses the direct peer.
- [ ] Set `ENDPOINT_ALLOW_PRIVATE=false` unless you route to models on loopback or a private
      network.
- [ ] Restrict `/metrics` to your monitoring network.
- [ ] Create the first owner with `smartrouter create-owner`, then invite other users with the
      least role they need.
- [ ] Rotate endpoint keys from the Endpoints page; rotate gateway keys by creating a new key,
      deploying it, then revoking the old one.
- [ ] Never run `make demo` or `seed-demo` against a real deployment.
