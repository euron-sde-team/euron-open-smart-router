# Security Policy

SmartRouter sits in front of your LLM providers, holds their API keys and decides which
model sees each request, so we take reports seriously and respond quickly.

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.x (latest minor) | Yes |
| Older minors | No |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository ("Security" tab, "Report a vulnerability").

Please include:

- the affected component (`apps/api`, `apps/web`, `infra/terraform/...`) and version or commit,
- a description of the issue and its impact,
- steps to reproduce, or a proof of concept,
- any suggested mitigation.

What to expect:

| Stage | Target |
| ----- | ------ |
| Acknowledgement | within 3 business days |
| Initial assessment and severity | within 7 business days |
| Fix or mitigation for critical and high issues | within 30 days |
| Coordinated public disclosure | after a fix is released, credited to you unless you prefer otherwise |

## Scope

In scope: the code in this repository and the infrastructure it provisions with its
default settings. We are especially interested in:

- any way to make the router send a request to a model its policy excludes (allowed
  models, capabilities, context window, an open circuit), or to switch a pinned model,
- any way for a real (non-simulated) endpoint to receive the benchmark answer key,
- any way to read another project's decisions or analytics with a gateway key,
- any way to recover a provider credential or a gateway key from the API, logs or database,
- authentication or authorization bypasses in the dashboard API.

Out of scope:

- **The demo seed.** `make demo` creates well-known demo passwords on purpose and refuses
  to run with `ENVIRONMENT=production`.
- **The mock LLM** (`apps/api/src/mockllm`), a test fixture that is never deployed to AWS.
- Findings that require a compromised host, compromised AWS account, or leaked deployment secrets.
- Denial of service through resource exhaustion of a deployment you operate yourself.

## Hardening checklist for operators

See [docs/architecture/security.md](docs/architecture/security.md#operator-checklist).
