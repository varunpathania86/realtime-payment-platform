#!/usr/bin/env bash
# Single-cluster entry point; retain saved feature choices and delegate the existing dev loop.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
export MINIKUBE_PROFILE="${MINIKUBE_PROFILE:-rpp}"
export MINIKUBE_CPUS="${MINIKUBE_CPUS:-6}"
export MINIKUBE_MEMORY="${MINIKUBE_MEMORY:-14336}"
[[ "$MINIKUBE_PROFILE" =~ ^[a-z][a-z0-9-]{0,39}$ ]] || {
  echo "Invalid MINIKUBE_PROFILE" >&2
  exit 1
}
state="$REPO_ROOT/.local/$MINIKUBE_PROFILE"
case "${1:-up}" in
  stop)
    minikube stop -p "$MINIKUBE_PROFILE"
    exit
    ;;
  clean)
    refuse_fenced_profile "$MINIKUBE_PROFILE"
    base="${MINIKUBE_PROFILE%-?}"
    if [[ "$MINIKUBE_PROFILE" =~ -[123]$ ]] && [ -f "$REPO_ROOT/.local/$base-multi/owned" ]; then
      echo "Profile belongs to a multi-cluster lab; use make dev-clean MULTI_CLUSTER=3" >&2
      exit 1
    fi
    if [ -L "$REPO_ROOT/.local" ] || [ -L "$state" ]; then
      echo "Refusing cleanup through a symlink: $state" >&2
      exit 1
    fi
    echo "Deleting profile '$MINIKUBE_PROFILE' and its data, including $state."
    minikube delete -p "$MINIKUBE_PROFILE"
    if [ -d "$state" ]; then
      rm -rf -- "$state"
    fi
    exit
    ;;
  up) refuse_fenced_profile "$MINIKUBE_PROFILE" ;;
  *) echo "Usage: run-local.sh {up|stop|clean}" >&2; exit 2 ;;
esac
mkdir -p "$state"
if [ ! -e "$state/events.tfvars.json" ]; then
  cat > "$state/events.tfvars.json" <<'JSON'
{
  "streaming_enabled": true,
  "analytics_enabled": true,
  "lakehouse_enabled": true,
  "bi_enabled": true,
  "ai_enabled": true,
  "kafka_host_port": 19092
}
JSON
fi
echo "Starting one cluster: $MINIKUBE_PROFILE ($MINIKUBE_CPUS CPUs, $MINIKUBE_MEMORY MiB)."
echo "Using saved features in $state/events.tfvars.json."
echo "The full combined stack needs host memory beyond the cluster allocation; its combined fit is not certified."
bash "$REPO_ROOT/scripts/local-dev.sh" up
echo "Browser access: make dev-access MINIKUBE_PROFILE=$MINIKUBE_PROFILE"
