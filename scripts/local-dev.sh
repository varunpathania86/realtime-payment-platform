#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
export PATH="$BIN_DIR:$PATH"
cd "$REPO_ROOT"

PROFILE="${MINIKUBE_PROFILE:-rpp}"
[[ "$PROFILE" =~ ^[a-z][a-z0-9-]{0,39}$ ]] || { echo "Invalid MINIKUBE_PROFILE" >&2; exit 1; }
LOCAL_PORT="${LOCAL_PORT:-8080}"
if ! [[ "$LOCAL_PORT" =~ ^[0-9]{1,5}$ ]] || ((10#$LOCAL_PORT < 1 || 10#$LOCAL_PORT > 65535)); then
  echo "LOCAL_PORT must be between 1 and 65535" >&2
  exit 1
fi
STATE_DIR="$REPO_ROOT/.local/$PROFILE"
TF_ROOT="$REPO_ROOT/platform/terraform/environments/local"
export TF_DATA_DIR="$STATE_DIR/terraform"
export TF_VAR_kube_context="$PROFILE"
export TF_VAR_kube_config_path="${KUBECONFIG:-$HOME/.kube/config}"
PYTHON="$REPO_ROOT/.venv/bin/python"

modules() {
  "$PYTHON" scripts/module_catalog.py local-modules
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
  local name path tag image commit inventory selected
  selected=$(modules)
  mkdir -p "$STATE_DIR"
  inventory=$(mktemp "$STATE_DIR/images.XXXXXX")
  trap 'rm -f "$inventory"' EXIT
  commit=$(git rev-parse HEAD)
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    path="modules/$name"
    tag=$(
      {
        printf '%s\n' "$commit"
        sha256sum .tool-versions
        git ls-files --cached --others --exclude-standard --deduplicate -z -- "$path" |
          sort -z |
          while IFS= read -r -d '' file; do
            printf '%s\0' "$file"
            if [ -f "$file" ]; then sha256sum "$file"; else printf 'deleted\n'; fi
          done
      } | sha256sum | cut -c1-16
    )
    image="rpp-local/$name:dev-$tag"
    docker build --build-arg "GO_VERSION=$(pinned_version go)" \
      --build-arg "NODE_VERSION=$(pinned_version nodejs)" \
      --build-arg "VERSION=dev-$tag" --build-arg "COMMIT=$commit" -t "$image" "$path"
    minikube -p "$PROFILE" image load "$image"
    printf '%s\t%s\n' "$name" "$image" >> "$inventory"
  done <<< "$selected"
  [ -s "$inventory" ] || { echo "No catalog modules with Dockerfiles and charts found" >&2; exit 1; }
  "$PYTHON" - "$inventory" "$STATE_DIR/images.tfvars.json" <<'PY'
import json
from pathlib import Path
import sys

images = dict(line.split() for line in Path(sys.argv[1]).read_text().splitlines())
Path(sys.argv[2]).write_text(json.dumps({"local_images": images}, indent=2) + "\n")
PY
)

plan() {
  require_images
  tf plan -input=false -var-file="$STATE_DIR/images.tfvars.json" -out="$STATE_DIR/tfplan"
}

apply() {
  plan
  tf apply -input=false "$STATE_DIR/tfplan"
}

smoke() (
  mkdir -p "$STATE_DIR"
  local pid
  kubectl --context "$PROFILE" -n platform port-forward --address 127.0.0.1 service/traefik "$LOCAL_PORT:80" \
    > "$STATE_DIR/smoke-forward.log" 2>&1 &
  pid=$!
  trap 'kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true' EXIT
  local ready=false
  for ((attempt = 0; attempt < 30; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      cat "$STATE_DIR/smoke-forward.log" >&2
      echo "Cannot start ingress port-forward; stop make dev-access or choose another LOCAL_PORT." >&2
      return 1
    fi
    if grep -q 'Forwarding from' "$STATE_DIR/smoke-forward.log"; then ready=true; break; fi
    sleep 1
  done
  [ "$ready" = true ] || { echo "Timed out starting ingress port-forward" >&2; return 1; }
  "$PYTHON" scripts/smoke.py --url "http://127.0.0.1:$LOCAL_PORT" --images "$STATE_DIR/images.tfvars.json"
)

charts() {
  local name selected
  selected=$(modules)
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
    init
    tf destroy -input=false -auto-approve -var-file="$STATE_DIR/images.tfvars.json"
    ;;
  access)
    exec kubectl --context "$PROFILE" -n platform port-forward --address 127.0.0.1 service/traefik "$LOCAL_PORT:80"
    ;;
  smoke) smoke ;;
  status)
    kubectl --context "$PROFILE" get namespaces
    kubectl --context "$PROFILE" -n apps get deployments,pods,services,ingresses
    ;;
  *) echo "Usage: $0 up|update|images|init|plan|apply|validate|charts|smoke|access|status|down" >&2; exit 2 ;;
esac
