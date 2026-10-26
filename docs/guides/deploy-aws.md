# Deploy to AWS

The Terraform in `infra/terraform` describes a complete production deployment. The
maintainers develop and test against the local emulator only: nothing in this project has
been applied to a real AWS account. The AWS environments are kept valid
(`make tf-validate tf-lint tf-security`) on every change, and the whole `aws-dev` environment
is planned, applied, re-planned and destroyed against Floci (`make tf-aws-on-floci`, see
[testing.md](testing.md#aws-environment-on-floci)). This guide describes how you would apply
it in your own account. Review the plan before you apply it.

## Environments

| Environment | What it is |
| --- | --- |
| `envs/local-floci` | KMS, storage and secrets on the Floci emulator. Applied automatically by `make up` |
| `envs/aws-dev` | The full stack, cost-lean: two availability zones, a single NAT gateway, single-AZ database, one cache node, no deletion protection, small autoscaling ceilings, 14-day log retention |
| `envs/aws-prod` | The full stack, resilient: three availability zones with a NAT gateway each, VPC interface endpoints, Multi-AZ database with more storage and 14-day backups, a primary and replica cache with failover, deletion protection, a 30-day KMS deletion window and 365-day log retention |

## What gets created

| Module | Resources |
| --- | --- |
| `network` | VPC across two or three availability zones, public and private subnets, NAT, flow logs |
| `kms` | Customer-managed key with rotation |
| `storage` | Private, TLS-only bucket for benchmark result files, KMS-encrypted, files expire after `expiration_days` (365 by default, equal to the API's `BENCHMARK_RESULTS_DAYS`) |
| `secrets` | Session secret, API key pepper and connection strings in Secrets Manager, injected by ECS |
| `database` | RDS PostgreSQL 16 (encrypted, private, automated backups, `rds.force_ssl`) |
| `cache` | ElastiCache Valkey with TLS, an AUTH token and `noeviction` (breaker state and the decision stream are never evicted) |
| `ecr` | Repositories for the api and web images |
| `service` | ECS Fargate cluster; api, worker and web services behind one ALB with WAF; a `migrate` task definition for one-off commands |
| `observability` | CloudWatch log groups, a dashboard and alarms to an SNS topic (see [operations.md](operations.md#cloudwatch)) |

The ALB sends `/v1/*`, `/api/*`, `/healthz` and `/readyz` to the api, and everything else
to the dashboard. `/metrics` is forwarded only from `metrics_allowed_cidrs`. Each module has
a README under `infra/terraform/modules/` with every input and output.

The api, worker and migrate tasks run with `ENVIRONMENT=production`,
`SMARTROUTER_CLOUD=aws`, `KEY_MODE=kms`, `STORAGE=s3`, `COOKIE_SECURE=true`,
`TRUSTED_PROXY_HOPS=1` (the ALB appends the client address to `X-Forwarded-For`) and the
bucket, key alias and CORS origin wired from the modules. Add other non-secret settings from
[configuration.md](configuration.md) through `api_extra_environment`.

## Variables

Copy `terraform.tfvars.example` to `terraform.tfvars` (git-ignored) in the environment you
deploy:

| Variable | Meaning |
| --- | --- |
| `region` | AWS region (example: `us-east-1`) |
| `name_prefix` | Prefix for every resource; the bucket name derives from it, so make it globally unique |
| `hostname` | Public host name, for example `smartrouter.example.com` |
| `certificate_arn` | ACM certificate in the same region covering `hostname` |
| `route53_zone_id` | Optional: creates the alias record. Remove it and CNAME `hostname` to the `load_balancer_dns_name` output instead |
| `image_tag` | The tag the services run (example: `0.1.0`). `make aws-deploy` overrides it with its `TAG` |
| `alarm_email` | Optional: e-mail subscription for alarms |
| `circuit_open_threshold` | Optional: alarm when failures on open circuits exceed this per 5 minutes (example: `20`) |
| `metrics_allowed_cidrs` | Optional: who may read `/metrics` through the ALB |
| `api_extra_environment` | Optional: extra non-secret API settings |
| `db_instance_class`, `cache_node_type` | Defaults `db.t4g.small` and `cache.t4g.micro` |

For shared state, copy `backend.tf.example` to `backend.tf`: S3 with encryption and
S3-native locking (`use_lockfile`). State contains generated secrets (the database password,
the cache AUTH token, the signing secrets), so the bucket must be private, versioned and
encrypted.

## Steps

You need Terraform, Docker with buildx, the AWS CLI, `python3`, `openssl` (for
`aws-admin`) and credentials for the target account. `make aws-plan ENV=<env>` shows the plan
first.

```bash
cp infra/terraform/envs/aws-dev/terraform.tfvars.example infra/terraform/envs/aws-dev/terraform.tfvars
#   set name_prefix, hostname, certificate_arn, route53_zone_id
cp infra/terraform/envs/aws-dev/backend.tf.example infra/terraform/envs/aws-dev/backend.tf
#   recommended: S3 state with locking

make aws-deploy ENV=aws-dev                       # or ENV=aws-prod; TAG defaults to the git short sha
make aws-admin ENV=aws-dev EMAIL=you@example.com  # the first dashboard owner
```

`make aws-deploy ENV=<env> TAG=<tag>` runs `scripts/aws-deploy.sh`, which:

1. applies the image registries first (`-target=module.ecr`), so there is somewhere to push;
2. builds the api and web images for `linux/arm64` (the tasks run on Graviton) and pushes
   them with `TAG`;
3. applies everything else with `image_tag=TAG`;
4. runs the database migrations as a one-off ECS task on the `migrate` task definition;
5. waits until the api, worker and web services are stable, then prints the `url` output.

It stops at the first failing step, and warns when the working tree has uncommitted changes
(the images are built from it as it is). Run it again with a new `TAG` to roll out a new
version.

`make aws-admin ENV=<env> EMAIL=<address>` runs `create-owner` as a one-off task with a
generated password and prints that one-time password: change it after you sign in. Running
it again for the same address resets the password.

`make aws-migrate ENV=<env>` runs only the migrations, as the same one-off task. Both
targets use `scripts/aws-run-task.sh`, which starts the task from the `run_task` output,
waits for it to stop, prints its recent logs and fails on a non-zero exit code.

Then open the `url` output, sign in, and:

1. Add your real endpoints on the **Endpoints** page (OpenAI-compatible, Anthropic, or your
   TokenShield gateway) and **Test connection**.
2. Add models on the **Models** page with prices, context windows, capabilities and a
   typical latency.
3. Create policies, projects and keys.
4. Run a benchmark from the **Benchmarks** page so the router ranks by measured quality
   instead of tier priors (see [operations.md](operations.md#re-run-benchmarks-after-a-catalog-change)).

## Notes

- The benchmark suite sends real requests to every model and every policy: on real
  providers it costs money. Run it with `--models` or a subset of policies first.
- Never run `seed-demo` against a real deployment (it refuses in production).
- Outputs: `url`, `load_balancer_dns_name`, `cluster_name`, `services`, `ecr_repositories`,
  `run_task`, `exports_bucket`, `kms_key_alias`, `dashboard_name`.
- For a cheaper trial, use [local against AWS](local-with-aws.md): local containers with a
  real KMS key and bucket.
