# Architecture Decision Records

Each ADR captures one significant decision: the context that forced it, what we chose, and what
it costs us. ADRs are immutable once accepted. To revisit one, write a new ADR that supersedes it.

| ADR | Decision | Status |
| --- | -------- | ------ |
| [0001](0001-single-tenant-deployments.md) | One deployment serves one organisation; teams, projects and keys live inside it, health and quality are shared | Accepted |
| [0002](0002-deterministic-explainable-request-analysis.md) | Request analysis is rule-based, deterministic and explains itself with signals | Accepted |
| [0003](0003-integer-nano-dollars.md) | Money is integer nano-dollars everywhere inside the system | Accepted |
| [0004](0004-decisions-through-a-redis-stream.md) | Routing decisions go through a Redis Stream, drained to Postgres by workers | Accepted |
| [0005](0005-local-aws-emulation.md) | Local AWS emulator (Floci), provisioned by the same Terraform as production | Accepted |
| [0006](0006-cross-fitted-benchmark-and-expected-quality.md) | The benchmark is cross-fitted, and the router ranks by quality expected over difficulty | Accepted |
| [0007](0007-python-fastapi-and-nextjs.md) | Python and FastAPI for the gateway, Next.js for the dashboard | Accepted |
| [0008](0008-tokenshield-integration-without-a-second-ledger.md) | TokenShield owns budgets; SmartRouter reads its cost header and honours its 402 | Accepted |
| [0009](0009-circuit-breakers-in-redis-lua.md) | Circuit breakers and health live in Redis, updated by one Lua script, shared by every replica | Accepted |

**Template:** Status / Date, then Context, Decision, Consequences (good and bad), and
Alternatives considered.
