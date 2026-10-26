# Integrate an application

SmartRouter speaks the OpenAI chat-completions API. Point any OpenAI SDK, LangChain,
LlamaIndex or plain HTTP client at `<gateway>/v1` with a SmartRouter key (`tsr_...`, created
on the API keys page), and send `model="auto"`. Nothing else changes.

## Choosing what `model` means

| `model` | What happens |
| --- | --- |
| `auto` | Routed by a policy: the `x-smartrouter-policy` header if sent, else the project's default policy, else the global default |
| `auto:<policy>` | Routed by that policy, for example `auto:cost`, `auto:quality` or `auto:support-bot`. Beats the header and the project default |
| A model alias, such as `gpt-fast` | Passed through to that model with no routing and no fallback. The decision is still logged, with policy `passthrough`. SmartRouter never switches a named model silently |

`GET /v1/models` lists what a key can send: `auto` (with the policy it resolves to for this
key's project), one `auto:<policy>` per policy, then every model alias with its tier and
context window.

Runnable clients for the local stack live in [`examples/`](../../examples/README.md):
[Python](../../examples/python/route.py) and [Node](../../examples/node/route.mjs), both with
the OpenAI SDK. Each sends three requests with `model="auto"`, prints which model answered,
why and at what cost, and rates the last answer through `/v1/feedback`.

## Python

```python
from openai import OpenAI

client = OpenAI(base_url="https://smartrouter.example.com/v1", api_key="tsr_...")
raw = client.chat.completions.with_raw_response.create(
    model="auto:support-bot",
    messages=[{"role": "user", "content": "Summarise this ticket for the next agent"}],
)
resp = raw.parse()
print(resp.model)                                # the model that answered
print(raw.headers["x-smartrouter-request-id"])   # keep it to send feedback later
```

## TypeScript

```ts
import OpenAI from "openai";

const client = new OpenAI({ baseURL: "https://smartrouter.example.com/v1", apiKey: "tsr_..." });
const stream = await client.chat.completions.create(
  { model: "auto", messages: [{ role: "user", content: "Review this diff for bugs" }], stream: true },
  { headers: { "x-smartrouter-policy": "code-review", "x-smartrouter-task": "coding" } },
);
for await (const chunk of stream) process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
```

## curl

```bash
curl -i https://smartrouter.example.com/v1/chat/completions \
  -H "authorization: Bearer tsr_..." \
  -H "content-type: application/json" \
  -H "x-smartrouter-complexity: 5" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Prove that the square root of 2 is irrational"}]}'
```

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/v1/chat/completions` | Streaming and non-streaming. Body up to 8 MB; `messages` must be a non-empty list |
| GET | `/v1/models` | Routes (`auto`, `auto:<policy>`) and models |
| POST | `/v1/feedback` | Rate the answer to a routed request |

## Request headers

| Header | Effect |
| --- | --- |
| `Authorization: Bearer tsr_...` (or `x-api-key`) | Required. The key decides the project, its default policy and its limits |
| `x-smartrouter-policy` | Policy for `model="auto"`. Ignored when `model` is `auto:<policy>` or a concrete alias |
| `x-smartrouter-task` | Override the detected task: `coding`, `math`, `reasoning`, `extraction`, `classification`, `summarization`, `translation` or `chat`. Other values are ignored |
| `x-smartrouter-complexity` | Override the detected difficulty, `1` to `5`. When you set it, the router trusts it and ranks by the quality score for exactly that difficulty band (easy 1 to 3, hard 4 and 5) instead of an expectation over both |

Without overrides, a deterministic analyzer reads the messages and records the signals it
used (a code fence, a maths verb, "one word", length, and so on). Overrides are recorded as
signals too, so the decision log shows who decided.

## Response headers

| Header | Meaning |
| --- | --- |
| `x-smartrouter-request-id` | Find the request in the Decisions page; send it to `/v1/feedback` |
| `x-smartrouter-model` | The model that answered (the body's `model` says the same) |
| `x-smartrouter-policy` | The policy that decided, or `passthrough` for a pinned model |
| `x-smartrouter-strategy` | `cost`, `latency`, `quality`, `balanced` or `passthrough` |
| `x-smartrouter-task`, `x-smartrouter-complexity` | What the analyzer concluded (or your overrides) |
| `x-smartrouter-attempts` | Models actually called, including failed ones |
| `x-smartrouter-fallbacks` | Comma-separated models that failed before the one that answered. Absent when the first one answered |
| `x-smartrouter-reason` | Why the first choice won (ASCII, at most 180 characters) |
| `x-smartrouter-estimated-cost-usd` | The ranking's cost estimate for the selected model |
| `x-smartrouter-baseline-model` | The model savings are measured against: the policy's baseline, else the most expensive model that could have served the request |
| `x-smartrouter-cost-usd` | Actual cost: from `x-tokenshield-cost-usd` behind TokenShield, else provider-reported tokens at catalog prices |
| `x-smartrouter-baseline-cost-usd` | The same tokens at the baseline model's prices |

For streamed responses the headers are sent before the tokens exist, so
`x-smartrouter-cost-usd` and `x-smartrouter-baseline-cost-usd` are absent; the decision log
has the actual cost once the stream ends.

## Streaming

Send `"stream": true`. SmartRouter asks OpenAI-compatible endpoints for a final usage chunk
and translates Anthropic streams to the OpenAI shape. Fallback is possible only until the
first chunk arrives: once anything has reached your client, a failure ends the stream with
an SSE error event (`code: upstream_error`) followed by `[DONE]`, and is logged as
`stream_interrupted`. A client that disconnects early is logged as `client_disconnected`.

## Feedback

Rate an answer with the request id from the response headers:

```bash
curl https://smartrouter.example.com/v1/feedback \
  -H "authorization: Bearer tsr_..." -H "content-type: application/json" \
  -d '{"request_id":"req_...","score":1,"comment":"Exactly what the student needed"}'
```

`score` is `1` (good) or `-1` (bad); `comment` is optional (up to 2,000 characters). A key
can only rate its own project's requests (`400 not_your_request` otherwise). Feedback appears
in the decision drawer. It does not change routing in 0.1.0; quality comes from benchmarks.

The `request_id` must be one SmartRouter routed: an unknown id is `404 not_found`. The
decision row is written by a worker moments after the response, so the gateway also keeps a
marker for each request id for a day; feedback sent straight after the request is accepted
before the row exists. Feedback is rate limited per key, at the key's requests-per-minute
limit, or 600 per minute when the key has none (`429 rpm_limit` with `Retry-After`).

## Errors

Errors use the OpenAI envelope with extra structure:
`{"error": {"code", "type", "message", "details"}}`. SDKs surface `message` and `code`.

| HTTP | `code` | When |
| --- | --- | --- |
| 400 | `invalid_json`, `invalid_request`, `body_too_large`, `invalid_score`, `not_your_request` | Malformed body, empty `messages`, body over 8 MB, bad feedback |
| 401 | `invalid_api_key`, `api_key_revoked`, `api_key_expired` | Bad or retired key |
| 402 | `budget_exceeded` | TokenShield refused on budget and the policy could not (or may not) downgrade. TokenShield's own error is passed through, with SmartRouter's attempts added under `details` |
| 403 | `project_unavailable` | The key's project is archived |
| 404 | `model_not_found` | `model` is neither `auto`, `auto:<policy>` nor a configured alias. `details.routes` lists the valid routes |
| 404 | `not_found` | `POST /v1/feedback` with a `request_id` SmartRouter did not route |
| 404 | `policy_not_found` | The policy named by `auto:<policy>`, the header or the project does not exist, or no default policy is set. `details.policies` lists them |
| 422 | `no_eligible_model` | No model passes the policy's filters (capabilities, context, allowlist, ceilings, open circuits) or, with `on_no_eligible: reject`, the quality floor. `details.candidates` gives every model's reason. Nothing was called |
| 422 | `validation_error` | Request validation failed |
| 429 | `rpm_limit`, `concurrency_limit` | The key's limits; `Retry-After` is set |
| 429 | `upstream_rate_limited` | Exactly one model was called and its provider answered 429: a pinned model, a policy with fallback off, or no other candidate left. `Retry-After` is the provider's own value (`1` when it sent none); back off and retry. `details.attempts` holds the attempt |
| 4xx | provider code | The upstream refused the request itself (a 4xx other than 429 or a TokenShield 402). Not retried on another model; `details.upstream` holds the provider's error |
| 502 | `all_models_failed` | Every candidate failed, or the first one failed and fallback is off (except a lone 429, above). `details.attempts` is the chain, each with model, outcome, latency and status |
| 504 | `upstream_timeout` | The only model tried did not answer in time |

Every failure is logged as a decision too, with the error code and the attempts, so it can
be found by request id.

## Fallback rules

Fallback is ordered by rank and never silent: every attempt is in `x-smartrouter-fallbacks`
and in the decision log.

- Only when the policy has `allow_fallback` on, and up to its `max_attempts` (default 3).
- Only on failures that say nothing about the request: connection error, timeout, 429 and
  5xx. Any other 4xx goes back to you.
- A model whose circuit is open is never chosen; a half-open model gets exactly one probe
  request at a time and is skipped by others. Health is checked again just before each call,
  so a model whose circuit opened after ranking is skipped too ("circuit opened after
  ranking" in the attempts).
- A pinned model (not `auto`) is never replaced, and is called even when its circuit is
  open (while it is half-open, the one-probe rule above still applies). When its provider answers 429 you get `429 upstream_rate_limited` with the
  provider's `Retry-After`, so your client's usual back-off applies.

## Running behind TokenShield

[Euron Open TokenShield](https://github.com/euron-sde-team/euron-open-tokenshield) decides
whether a request may spend; SmartRouter decides which model spends it. Put TokenShield
between SmartRouter and your providers:

1. In TokenShield, create a project and a `tsk_...` key, and model aliases for the upstream
   models.
2. In SmartRouter, open **Endpoints**, add an endpoint with API style **TokenShield**, the
   TokenShield gateway's `/v1` base URL and the `tsk_...` key.
3. Under **Models**, add aliases on that endpoint whose upstream model is the TokenShield
   alias.

What changes for those models:

- **No double accounting.** The actual cost is read from TokenShield's
  `x-tokenshield-cost-usd` header (`cost_source: tokenshield` in the decision), and
  TokenShield's request id is kept as the decision's upstream request id.
- **Budget refusals.** A TokenShield `402 budget_exceeded` is treated as a refusal to spend
  on that model. With the policy's `budget_downgrade` on, SmartRouter retries only
  candidates that are cheaper than the refused one and still eligible under the policy; a
  more expensive model is skipped and recorded as such. With it off, or when no cheaper
  eligible model exists, the 402 goes back to your client as TokenShield sent it.

In the demo, `gpt-mini` is reached through the TokenShield gateway (mock) endpoint, and the
`cost` policy has `budget_downgrade` on.
