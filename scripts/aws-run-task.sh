#!/usr/bin/env bash
# Run a one-off `smartrouter` command on the api image as an ECS task, wait, print its logs.
#   scripts/aws-run-task.sh <aws-dev|aws-prod> migrate
#   SMARTROUTER_OWNER_PASSWORD=... scripts/aws-run-task.sh aws-dev create-owner --email you@example.com
# Environment variables named in TASK_ENV (space separated) are passed to the task.
set -euo pipefail
ENV="${1:?usage: aws-run-task.sh <env> <smartrouter command...>}"; shift
DIR="$(cd "$(dirname "$0")/.." && pwd)/infra/terraform/envs/$ENV"
spec=$(terraform -chdir="$DIR" output -json run_task)
get() { echo "$spec" | python3 -c "import json,sys; v=json.load(sys.stdin)['$1']; print(','.join(v) if isinstance(v, list) else v)"; }
cluster=$(get cluster); taskdef=$(get task_definition); container=$(get container)
overrides=$(python3 - "$container" "$@" <<'PY'
import json, os, sys
container, cmd = sys.argv[1], sys.argv[2:]
env = [{"name": k, "value": os.environ[k]} for k in os.environ.get("TASK_ENV", "").split() if k in os.environ]
print(json.dumps({"containerOverrides": [{"name": container, "command": cmd, "environment": env}]}))
PY
)
network="awsvpcConfiguration={subnets=[$(get subnets)],securityGroups=[$(get security_groups)],assignPublicIp=DISABLED}"
arn=$(aws ecs run-task --cluster "$cluster" --task-definition "$taskdef" --launch-type FARGATE \
  --network-configuration "$network" --overrides "$overrides" \
  --query 'tasks[0].taskArn' --output text)
echo "started $arn"
aws ecs wait tasks-stopped --cluster "$cluster" --tasks "$arn"
code=$(aws ecs describe-tasks --cluster "$cluster" --tasks "$arn" --query 'tasks[0].containers[0].exitCode' --output text)
group=$(aws ecs describe-task-definition --task-definition "$taskdef" --query 'taskDefinition.containerDefinitions[0].logConfiguration.options."awslogs-group"' --output text)
aws logs tail "$group" --since 15m --format short | tail -40
echo "exit code: $code"
[[ "$code" == "0" ]]
