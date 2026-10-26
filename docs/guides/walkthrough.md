# Dashboard walkthrough

A tour of every screen with the demo data. Start the stack and seed it first
([quickstart](quickstart-local.md)): `make up && make demo`, then sign in at
http://localhost:4210 as `admin@smartrouter.local` with the password `make demo` printed.

The sidebar groups the screens: **Monitor** (Overview, Decisions, Models, Benchmarks),
**Route** (Simulator, Policies), **Configure** (Projects, API keys, Endpoints) and
**Organisation** (Teams, Users, Audit log, Settings). The bell in the top bar holds health
alerts, such as a circuit opening. Every screen works in light and dark mode and at phone
width.

Roles decide what you can do: viewers read everything, admins change configuration, owners
also manage users. The demo seeds one of each: `admin@smartrouter.local` (owner),
`ana.lima@example.com` (admin) and `sam.okafor@example.com` (viewer), all with the demo
password.

## Overview

The headline numbers for the selected time range (picker in the top bar): **Saved** and
**Savings rate** against each request's baseline model, **Spend**, **Requests**, **Fallback
rate** and **Routing time**. Below them:

- **Routed cost vs baseline** over time, or **Requests by outcome** (answered first time,
  after a fallback). With 60 days of demo history, try a long range to see traffic grow.
- **Where traffic goes**: share of requests, routed cost and saving split by model, task,
  policy or project. Switch to task to see classification go to cheap models and coding to
  strong ones.
- **Model health**: the live circuit state of every model.
- **Open alerts**: the demo has one for the `gpt-pro` outage. Mark it as read here or in the
  bell.

**Try:** compare the saving by policy: `code-review` saves little (it is quality-first by
design), `support-bot` and `cost` save the most.

## Decisions

Every gateway request, newest first: time, task, policy, model (with an arrow when it fell
back), status, cost, saving and latency. Filter by model, policy, task or status, search by
request id (`req_...`), and switch on refresh every 10 seconds to watch live traffic. Failed
and refused requests are here too.

Open a row for the **why this model** drawer:

- **What the request looked like**: the detected task and complexity, the signals behind
  them, and any caller overrides.
- **Why this model**: every candidate with its score and its cost, latency and quality
  components, or the reason it was filtered ("circuit open: recent failures", "coding
  quality 0.62 below the floor 0.85", "no tool calling"). When nothing cleared the floor,
  the drawer says the floor was relaxed.
- **Cost**: estimated, actual and baseline, and what was saved. Behind TokenShield the
  actual cost comes from TokenShield.
- **Details**: requested model, attempts and the fallback chain, latency, upstream request
  id and feedback.

**Try:** filter by model `gpt-pro` around the demo outage (about six days ago, at 14:10),
or find the live requests the seed sent with the `quality` policy while `gpt-pro` was
failing: the drawer shows the failed attempts and the model that answered instead.

## Models

Two tabs.

**Catalog and health** lists everything the router can choose from: alias, tier, prices per
1M input and output tokens, capabilities (tools, vision, JSON) and live health (circuit
state, p50 and p95 latency and error rate over the last five minutes, or the latency prior
before any traffic). The row menu edits price and capabilities, disables a model (it leaves
routing at once), and, when a circuit is not closed, **Close circuit now**.

**Quality** is the quality table the router ranks by: one cell per model and task, easy
requests (complexity 1 to 3) on the left and hard (4 and 5) on the right. The demo's cells
come from the benchmark run. Admins can **Set quality by hand**; manual scores are marked M
and are replaced by the next benchmark.

**Try:** look at `llama-local`: free and fine for classification, weak on hard coding. That
is why the `cost` policy sends it simple work and never PR reviews.

## Benchmarks

Reproducible proof of what routing saves. A run sends every case of the suite (160 graded
cases across eight tasks) to every model, then routes every case through every policy, and
grades each answer.

- **Quality and cost, side by side**: each model and policy as a bar of quality and cost
  relative to the baseline (the most expensive model, `claude-pro` in the demo).
- **Policies**: quality, change against the baseline in points, cost saved and the models
  each policy chose. In the demo run: `auto:balanced` 86.3% (1.9 points below the baseline's
  88.1%) for 19.8% less, `auto:quality` 89.4% (1.3 points above) for 5.7% less, and
  `auto:cost` 78.8% (9.4 points below) for 37.2% less.
- **Models**: each model's score. These became the quality table.
- **Runs**: history. **Download every answer** fetches the run's per-case results (gzipped
  JSON lines from object storage: one row per case per model and policy, with the answer and
  its grade).

**Run benchmark** queues a new run for the worker; it shows Queued, then Running, then the
results. Re-run after adding a model or changing prices.

## Simulator

Ask the router what it would do with a request without calling any model or spending
anything. Pick an example (Classify a review, Extract JSON, Hard coding task, Maths,
Translate) or paste a prompt, choose `auto`, `auto:<policy>` or a model, and optionally
set a system prompt, max tokens, task, complexity, or mark the request as needing tools or
JSON. The result shows the analysis (task, complexity, input tokens, output allowance,
quality floor, whether it was relaxed, routing time), the selected model, the estimated
saving and every candidate in fallback order with its reason.

**Try:** the "Hard coding task" example under `auto:cost` and then `auto:quality`.

## Policies

One card per policy with its strategy, quality floor, task floors, limits and flags
(budget downgrade, no fallback, rejects when nothing qualifies), and its traffic in the last
24 hours. **Create a policy** opens a form for every field described in
[configuration.md](configuration.md#routing-policies): strategy, floor, hard-request
boost, task floors, what to do when nothing clears the floor, latency and cost ceilings,
allowed and excluded models, fallback and max attempts, baseline model and budget
downgrade.

**Try:** create `homework` with strategy `cost` and a `math` floor of 0.85, then call it as
`model="auto:homework"` or test it in the Simulator.

## Projects

Each application that calls the gateway, with its team, default policy, requests, spend and
saving for the selected range. Open one to change its **Routing** (default policy for
`model="auto"`, or the global default), see its **API keys**, edit its settings, or archive it
(its keys stop working; history stays). In the demo, Alpha Tutor uses `balanced`, Support
Copilot `support-bot`, Code Review Agent `code-review`, Doc Insights `cost`, and Marketing
Studio the global default.

## API keys

Gateway keys, shown as `tsr_<prefix>_****` with their project, limits (requests per minute,
concurrent requests), status and last use. **Create key** shows the full key once. Revoke a
key and it is refused immediately. Show revoked keys with the toggle.

## Endpoints

The upstream APIs behind the models: OpenAI-compatible (also Ollama, vLLM, LM Studio, Groq),
Anthropic, or a TokenShield gateway. The demo has four, all on the mock provider and marked
Simulated (they may receive the benchmark answer key). **Test connection** calls the
endpoint's model listing. API keys are write-only and sealed at rest; the list shows only a
hint.

## Teams

Groups of projects, such as the demo's Learning Products, Customer Support, Platform and
Growth. Create, rename or delete a team; assign projects to a team from the project page.

## Users

People who can sign in, with role, access and last sign-in. Owners add users with an initial
password, change roles and disable access.

## Audit log

Every configuration change and sign-in, append-only: who did what, to which object, from
where. Filter by action, for example `policy.deleted`, `api_key.revoked` or `health.reset`.

## Settings

**System** shows how this install is configured, read from the running API: version,
environment, cloud, provider key sealing, export storage, circuit breaker settings, default
policy, inventory and dependency checks. Change these through environment variables
([configuration.md](configuration.md)). **Your account** shows your name, email and role, **Change password** sets a new one
(other sessions are signed out), and **Appearance** picks the theme for this browser.
