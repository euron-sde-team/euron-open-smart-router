#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# One-shot deployment to AWS:  scripts/aws-deploy.sh <aws-dev|aws-prod> <image-tag>
#
#   1. terraform apply the image registries (and the KMS key that encrypts them)
#   2. build the api and web images for linux/arm64 (Graviton) and push them
#   3. terraform apply everything else with that image tag
#   4. run the database migrations as a one-off ECS task
#   5. wait for the api, worker and web services to become stable
#
# Needs: terraform, docker (buildx), the AWS CLI, credentials for the target account, and
# infra/terraform/envs/<env>/terraform.tfvars (copy the .example next to it).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
ENV="${1:?usage: aws-deploy.sh <aws-dev|aws-prod> <image-tag>}"
TAG="${2:?usage: aws-deploy.sh <aws-dev|aws-prod> <image-tag>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/infra/terraform/envs/$ENV"

for bin in terraform docker aws python3; do command -v "$bin" >/dev/null || { echo "missing: $bin"; exit 1; }; done
[[ -f "$DIR/terraform.tfvars" ]] || { echo "create $DIR/terraform.tfvars from terraform.tfvars.example first"; exit 1; }
[[ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]] && echo "note: the working tree has uncommitted changes; the images are built from it as-is"

tf() { terraform -chdir="$DIR" "$@"; }
json_field() { python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }

echo "==> terraform init"
tf init -input=false >/dev/null

echo "==> 1/5 registries"
tf apply -input=false -auto-approve -var "image_tag=$TAG" -target=module.ecr

region=$(aws configure get region 2>/dev/null || true)
region="${AWS_REGION:-${region:-$(grep -E '^region' "$DIR/terraform.tfvars" | sed -E 's/.*"(.*)".*/\1/')}}"
repos_json=$(tf output -json ecr_repositories)
registry=$(echo "$repos_json" | json_field api | cut -d/ -f1)
aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry" >/dev/null

echo "==> 2/5 images ($TAG, linux/arm64)"
for app in api web; do
  repo=$(echo "$repos_json" | json_field "$app")
  docker buildx build --platform linux/arm64 -f "$ROOT/infra/docker/$app.Dockerfile" \
    -t "$repo:$TAG" --push "$ROOT/apps/$app"
done

echo "==> 3/5 infrastructure"
tf apply -input=false -auto-approve -var "image_tag=$TAG"

echo "==> 4/5 migrations"
"$ROOT/scripts/aws-run-task.sh" "$ENV" migrate

echo "==> 5/5 waiting for the services"
cluster=$(tf output -raw cluster_name)
services=$(tf output -json services | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).values()))")
aws ecs wait services-stable --region "$region" --cluster "$cluster" --services $services

echo
tf output url
cat <<NEXT

Deployed $TAG to $ENV. Next:
  make aws-admin ENV=$ENV EMAIL=you@example.com   # the first dashboard owner
  then add endpoints and models in the dashboard and run a benchmark.
NEXT
