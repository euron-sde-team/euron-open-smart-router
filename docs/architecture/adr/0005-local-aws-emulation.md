# ADR-0005: A local AWS emulator, provisioned by the same Terraform as production

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

SmartRouter uses S3 (per-case benchmark result files, presigned downloads), KMS (sealing endpoint
credentials with an encryption context) and Secrets Manager (the session secret and key pepper).
Asking every contributor or evaluator for an AWS account before they can see routing work is a
steep on-ramp, and mocking each SDK call would test the mocks, not the system.

## Decision

Run **[Floci](https://github.com/floci-io/floci)**, an MIT-licensed local AWS emulator, as a
Compose service. Point the unmodified AWS SDK at it with `AWS_ENDPOINT_URL`. A one-shot container
applies `infra/terraform/envs/local-floci`, which uses the same `storage`, `kms` and `secrets`
modules as `aws-dev` and `aws-prod`, before any application container starts. Postgres, Redis
and the mock LLM, which Floci does not back, run as plain containers locally; Postgres and Redis
map to RDS and ElastiCache in the AWS environments.

## Consequences

**Good**
- Clone to a working router with only Docker: `make up && make demo`.
- Application code has no emulator branches: everything is configuration (`AWS_ENDPOINT_URL`,
  `S3_FORCE_PATH_STYLE`, `S3_PUBLIC_ENDPOINT`).
- The Terraform runs on every local start, and the integration suite runs against the KMS and S3
  backends (`test_kms_seal_round_trip_with_context`, `test_s3_storage_put_get_presign`,
  `test_benchmark_cases_land_in_s3_with_presigned_link`; `make test-matrix` covers the backend
  combinations).
- The full production environment is exercised too: `make tf-aws-on-floci` plans, applies,
  re-plans and destroys `aws-dev` against a throwaway Floci, which catches plan-time bugs that
  `terraform validate` cannot see.

**Bad / caveats**
- Emulators prove API compatibility, not IAM. Least-privilege mistakes show up only on real AWS,
  which is what `aws-dev` is for.
- Floci does not persist every attribute, so a re-plan against the emulator can show a few
  in-place updates that real AWS would not.
- The benchmark's numbers locally come from the mock LLM, not from real providers (see
  [benchmarks.md](../benchmarks.md#what-the-simulation-proves-and-what-it-does-not)).

## Alternatives considered

- **Real AWS for development.** Cost and friction for every contributor.
- **LocalStack.** Similar role; Floci matches the sibling Euron projects and is MIT.
