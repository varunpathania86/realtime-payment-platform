#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
export PATH="$BIN_DIR:$PATH"
cd "$REPO_ROOT"

PROFILE="${MINIKUBE_PROFILE:-rpp}"
[[ "$PROFILE" =~ ^[a-z][a-z0-9-]{0,39}$ ]] || { echo "Invalid MINIKUBE_PROFILE" >&2; exit 1; }
case "${1:-}" in
  up|update|images|apply|down) refuse_fenced_profile "$PROFILE" ;;
esac
LOCAL_PORT="${LOCAL_PORT:-8080}"
if ! [[ "$LOCAL_PORT" =~ ^[0-9]{1,5}$ ]] || ((10#$LOCAL_PORT < 1 || 10#$LOCAL_PORT > 65535)); then
  echo "LOCAL_PORT must be between 1 and 65535" >&2
  exit 1
fi
DBX_LOCAL_PORT="${DBX_LOCAL_PORT:-44224}"
if ! [[ "$DBX_LOCAL_PORT" =~ ^[0-9]{1,5}$ ]] || ((10#$DBX_LOCAL_PORT < 1 || 10#$DBX_LOCAL_PORT > 65535)); then
  echo "DBX_LOCAL_PORT must be between 1 and 65535" >&2
  exit 1
fi
STATE_DIR="$REPO_ROOT/.local/$PROFILE"
EXTRA_VARS=()
if [ -f "$STATE_DIR/events.tfvars.json" ]; then
  EXTRA_VARS+=("-var-file=$STATE_DIR/events.tfvars.json")
fi
TF_ROOT="$REPO_ROOT/platform/terraform/environments/local"
export TF_DATA_DIR="$STATE_DIR/terraform"
export TF_VAR_kube_context="$PROFILE"
export TF_VAR_kube_config_path="${KUBECONFIG:-$HOME/.kube/config}"
export TF_VAR_streaming_restore_path="${STREAMING_RESTORE_PATH:-}"
export TF_VAR_lakehouse_restore_path="${LAKEHOUSE_RESTORE_PATH:-}"
export TF_VAR_access_port="$LOCAL_PORT"
PYTHON="$REPO_ROOT/.venv/bin/python"

modules() {
  local arguments=(--enabled-only)
  if [ -f "$STATE_DIR/events.tfvars.json" ] &&
    jq -e '.streaming_enabled == true' "$STATE_DIR/events.tfvars.json" >/dev/null; then
    arguments+=(--feature streaming)
  fi
  if [ -f "$STATE_DIR/events.tfvars.json" ] &&
    jq -e '.analytics_enabled == true' "$STATE_DIR/events.tfvars.json" >/dev/null; then
    arguments+=(--feature analytics)
  fi
  if [ -f "$STATE_DIR/events.tfvars.json" ] &&
    jq -e '.lakehouse_enabled == true' "$STATE_DIR/events.tfvars.json" >/dev/null; then
    arguments+=(--feature lakehouse)
  fi
  if [ -f "$STATE_DIR/events.tfvars.json" ] &&
    jq -e '.bi_enabled == true' "$STATE_DIR/events.tfvars.json" >/dev/null; then
    arguments+=(--feature bi)
  fi
  if [ -f "$STATE_DIR/events.tfvars.json" ] &&
    jq -e '.ai_enabled == true' "$STATE_DIR/events.tfvars.json" >/dev/null; then
    arguments+=(--feature ai)
  fi
  "$PYTHON" scripts/module_catalog.py local-modules "${arguments[@]}"
}

tf() {
  terraform -chdir="$TF_ROOT" "$@"
}

require_images() {
  [ -s "$STATE_DIR/images.tfvars.json" ] ||
    { echo "No local image inventory for $PROFILE. Run make dev-images first." >&2; exit 1; }
}

init() {
  mkdir -p "$STATE_DIR"
  tf init -input=false -reconfigure -backend-config="path=$STATE_DIR/terraform.tfstate"
}

build_images() (
  local name path tag image commit inventory candidate selected dependency streaming=false object_store=false
  local -a dependencies sources contexts
  selected=$(modules)
  mkdir -p "$STATE_DIR"
  if [ -f "$STATE_DIR/events.tfvars.json" ] &&
    jq -e '.streaming_enabled == true' "$STATE_DIR/events.tfvars.json" >/dev/null; then
    streaming=true
    object_store=true
  fi
  # The assistant keeps original knowledge documents in MinIO even without Flink.
  if [ -f "$STATE_DIR/events.tfvars.json" ] &&
    jq -e '.ai_enabled == true' "$STATE_DIR/events.tfvars.json" >/dev/null; then
    object_store=true
  fi
  inventory=$(mktemp "$STATE_DIR/images.XXXXXX")
  candidate=$(mktemp "$STATE_DIR/images-candidate.XXXXXX")
  trap 'rm -f "$inventory" "$candidate"' EXIT
  commit=$(git rev-parse HEAD)
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    path="modules/$name"
    mapfile -t dependencies < <("$PYTHON" scripts/module_catalog.py dependencies --name "$name")
    sources=("$path")
    for dependency in "${dependencies[@]}"; do
      [ -n "$dependency" ] && sources+=("modules/$dependency")
    done
    tag=$(
      {
        printf '%s\n' "$commit"
        sha256sum .tool-versions
        git ls-files --cached --others --exclude-standard --deduplicate -z -- "${sources[@]}" |
          sort -z |
          while IFS= read -r -d '' file; do
            printf '%s\0' "$file"
            if [ -f "$file" ]; then sha256sum "$file"; else printf 'deleted\n'; fi
          done
      } | sha256sum | cut -c1-16
    )
    image="rpp-local/$name:dev-$tag"
    printf '%s\t%s\n' "$name" "$image" >> "$inventory"
  done <<< "$selected"
  [ -s "$inventory" ] || { echo "No catalog modules with Dockerfiles and charts found" >&2; exit 1; }
  "$PYTHON" - "$inventory" "$candidate" <<'PY'
import json
from pathlib import Path
import sys

images = dict(line.split() for line in Path(sys.argv[1]).read_text().splitlines())
Path(sys.argv[2]).write_text(json.dumps({"local_images": images}, indent=2) + "\n")
PY
  if [ "$streaming" = true ]; then
    "$PYTHON" scripts/streaming-control.py guard --profile "$PROFILE" --images "$candidate"
  fi
  if [ "$object_store" = true ]; then
    bash scripts/build-minio.sh
    minikube -p "$PROFILE" image load "rpp-runtime/minio:$(pinned_version minio)" --overwrite=false
  fi
  while IFS=$'\t' read -r name image; do
    path="modules/$name"
    tag="${image##*:}"
    contexts=()
    while IFS= read -r dependency; do
      [ -n "$dependency" ] && contexts+=(--build-context "$dependency=modules/$dependency")
    done < <("$PYTHON" scripts/module_catalog.py dependencies --name "$name")
    docker build "${contexts[@]}" --build-arg "GO_VERSION=$(pinned_version go)" \
      --build-arg "NODE_VERSION=$(pinned_version nodejs)" \
      --build-arg "FLINK_VERSION=$(pinned_version flink)" \
      --build-arg "FLINK_PYTHON_VERSION=$(pinned_version flink-python)" \
      --build-arg "FLINK_KAFKA_CONNECTOR_VERSION=$(pinned_version flink-kafka-connector)" \
      --build-arg "FLINK_KAFKA_CONNECTOR_SHA256=$(pinned_version flink-kafka-connector-sha256)" \
      --build-arg "CLICKHOUSE_VERSION=$(pinned_version clickhouse)" \
      --build-arg "RISK_PYTHON_VERSION=$(pinned_version risk-python)" \
      --build-arg "POLARIS_VERSION=$(pinned_version polaris)" \
      --build-arg "TRINO_VERSION=$(pinned_version trino)" \
      --build-arg "ICEBERG_VERSION=$(pinned_version iceberg)" \
      --build-arg "ICEBERG_FLINK_RUNTIME_SHA256=$(pinned_version iceberg-flink-runtime-sha256)" \
      --build-arg "ICEBERG_AWS_BUNDLE_SHA256=$(pinned_version iceberg-aws-bundle-sha256)" \
      --build-arg "HADOOP_CLIENT_VERSION=$(pinned_version hadoop-client)" \
      --build-arg "HADOOP_CLIENT_API_SHA256=$(pinned_version hadoop-client-api-sha256)" \
      --build-arg "HADOOP_CLIENT_RUNTIME_SHA256=$(pinned_version hadoop-client-runtime-sha256)" \
      --build-arg "SUPERSET_VERSION=$(pinned_version superset)" \
      --build-arg "SUPERSET_DIGEST=$(pinned_version superset-image-digest)" \
      --build-arg "AI_PYTHON_VERSION=$(pinned_version ai-python)" \
      --build-arg "LLAMA_CPP_VERSION=$(pinned_version llama-cpp)" \
      --build-arg "LLAMA_CPP_DIGEST=$(pinned_version llama-cpp-image-digest)" \
      --build-arg "VERSION=$tag" --build-arg "COMMIT=$commit" -t "$image" "$path"
    minikube -p "$PROFILE" image load "$image" --overwrite=false
  done < "$inventory"
  if [ -f "$STATE_DIR/events.tfvars.json" ] &&
    jq -e '.identity_enabled == true' "$STATE_DIR/events.tfvars.json" >/dev/null; then
    bash scripts/identity-runtime.sh build
    image=$(bash scripts/identity-runtime.sh image)
    minikube -p "$PROFILE" image load "$image" --overwrite=false
    "$PYTHON" - "$candidate" "$image" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
settings = json.loads(path.read_text())
settings["local_images"]["identity-provider"] = sys.argv[2]
path.write_text(json.dumps(settings, indent=2) + "\n")
PY
  fi
  if [ -f "$STATE_DIR/events.tfvars.json" ] &&
    jq -e '.dbx_enabled == true' "$STATE_DIR/events.tfvars.json" >/dev/null; then
    bash scripts/dbx-runtime.sh build
    image=$(bash scripts/dbx-runtime.sh image)
    minikube -p "$PROFILE" image load "$image" --overwrite=false
    "$PYTHON" - "$candidate" "$image" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
settings = json.loads(path.read_text())
settings["local_images"]["dbx-workbench"] = sys.argv[2]
path.write_text(json.dumps(settings, indent=2) + "\n")
PY
  fi
  mv "$candidate" "$STATE_DIR/images.tfvars.json"
)

plan() {
  require_images
  if [ -f "$STATE_DIR/events.tfvars.json" ] &&
    jq -e '.streaming_enabled == true' "$STATE_DIR/events.tfvars.json" >/dev/null; then
    "$PYTHON" scripts/streaming-control.py guard --profile "$PROFILE"
  fi
  "$PYTHON" scripts/local_database.py "$STATE_DIR/database.tfvars.json"
  tf plan -input=false -var-file="$STATE_DIR/images.tfvars.json" \
    -var-file="$STATE_DIR/database.tfvars.json" "${EXTRA_VARS[@]}" -out="$STATE_DIR/tfplan"
}

apply() {
  plan
  tf apply -input=false "$STATE_DIR/tfplan"
}

smoke() (
  require_images
  mkdir -p "$STATE_DIR"
  local pid port
  kubectl --context "$PROFILE" -n platform port-forward --address 127.0.0.1 service/traefik ":80" \
    > "$STATE_DIR/smoke-forward.log" 2>&1 &
  pid=$!
  trap 'kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true' EXIT
  local ready=false
  for ((attempt = 0; attempt < 30; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      cat "$STATE_DIR/smoke-forward.log" >&2
      echo "Cannot start temporary ingress port-forward for smoke checks." >&2
      return 1
    fi
    port=$(sed -nE 's/^Forwarding from 127\.0\.0\.1:([0-9]+) -> .*/\1/p' "$STATE_DIR/smoke-forward.log")
    if [ -n "$port" ]; then ready=true; break; fi
    sleep 1
  done
  [ "$ready" = true ] || { echo "Timed out starting ingress port-forward" >&2; return 1; }
  "$PYTHON" scripts/smoke.py --url "http://127.0.0.1:$port" --images "$STATE_DIR/images.tfvars.json" --wait-seconds 30
)

charts() {
  local name selected
  selected=$("$PYTHON" scripts/module_catalog.py local-modules)
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    helm lint "modules/$name/chart" --strict
    helm template "$name" "modules/$name/chart" --namespace apps |
      "$PYTHON" scripts/check_local_chart.py
  done <<< "$selected"
}

case "${1:-}" in
  images) build_images ;;
  init) init ;;
  plan) plan ;;
  apply) apply ;;
  validate)
    mkdir -p "$STATE_DIR"
    tf init -backend=false -input=false
    tf validate
    ;;
  charts) charts ;;
  up)
    scripts/cluster.sh up
    build_images
    init
    apply
    smoke
    echo "Local stack ready. Run make dev-access MINIKUBE_PROFILE=$PROFILE to browse it."
    ;;
  update)
    build_images
    init
    apply
    smoke
    ;;
  down)
    require_images
    "$PYTHON" scripts/local_database.py "$STATE_DIR/database.tfvars.json"
    init
    tf destroy -input=false -auto-approve -var-file="$STATE_DIR/images.tfvars.json" \
      -var-file="$STATE_DIR/database.tfvars.json" "${EXTRA_VARS[@]}"
    ;;
  access)
    exec kubectl --context "$PROFILE" -n platform port-forward --address 127.0.0.1 service/traefik "$LOCAL_PORT:80"
    ;;
  dbx-access)
    exec kubectl --context "$PROFILE" -n apps port-forward --address 127.0.0.1 \
      service/dbx-workbench "$DBX_LOCAL_PORT:4224"
    ;;
  dbx-status)
    kubectl --context "$PROFILE" -n apps rollout status deployment/dbx-workbench --timeout=30s
    ;;
  smoke) smoke ;;
  status)
    kubectl --context "$PROFILE" get namespaces
    kubectl --context "$PROFILE" -n apps get deployments,pods,services,ingresses
    ;;
  *) echo "Usage: $0 up|update|images|init|plan|apply|validate|charts|smoke|access|dbx-access|dbx-status|status|down" >&2; exit 2 ;;
esac
