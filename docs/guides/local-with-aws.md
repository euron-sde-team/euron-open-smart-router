# Local containers against real AWS

`make up-aws` runs the same containers as `make up`, but against a real KMS key and S3
bucket instead of the emulator. Postgres and Redis stay local containers. Use it to check
IAM and KMS behaviour that an emulator cannot prove. Like the full deployment, this path is
validated but has not been run against a real account by the maintainers; everything they
test runs on Floci.

```bash
cd infra/terraform/envs/aws-dev
cp terraform.tfvars.example terraform.tfvars      # see deploy-aws.md
terraform init
terraform apply -target=module.kms -target=module.storage
terraform output exports_bucket kms_key_alias
cd ../../../..
cp deploy/compose/.env.aws.example deploy/compose/.env.aws   # fill EXPORTS_BUCKET and KMS_KEY_ID
make up-aws
```

`deploy/compose/.env.aws` (git-ignored) holds only resource names:

| Variable | Value |
| --- | --- |
| `AWS_REGION` | The region you applied in (example: `us-east-1`) |
| `AWS_PROFILE` | Optional, default `default` |
| `EXPORTS_BUCKET` | The `exports_bucket` output |
| `KMS_KEY_ID` | The `kms_key_alias` output |

Credentials come from your AWS CLI profile: `~/.aws` is mounted read-only into the migrate,
api and worker containers, and no static keys are written anywhere. The overlay
(`deploy/compose/compose.aws.yaml`) sets `SMARTROUTER_CLOUD=aws`, `KEY_MODE=kms` and
`STORAGE=s3`. Do not set `AWS_ENDPOINT_URL` in this mode: the API refuses it under
`SMARTROUTER_CLOUD=aws` in production, and it would point the SDK away from AWS.

Endpoint API keys are then sealed with your KMS key, and benchmark result files land in
your bucket. `make down` stops the containers; the KMS key and bucket stay until you destroy
them with Terraform.
