#!/usr/bin/env bash
# Manage the local Minikube cluster. Usage: scripts/cluster.sh up|status|down
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
reexec_with_docker_group "$@"
export PATH="$BIN_DIR:$PATH"

PROFILE="${MINIKUBE_PROFILE:-rpp}"
CPUS="${MINIKUBE_CPUS:-4}"
MEMORY="${MINIKUBE_MEMORY:-8192}"
DISK="${MINIKUBE_DISK:-40g}"

case "${1:-}" in
  up)
    docker info >/dev/null 2>&1 || { echo "Docker is not reachable: see docs/developer-setup.md" >&2; exit 1; }
    minikube start -p "$PROFILE" --driver=docker --cpus="$CPUS" --memory="$MEMORY" --disk-size="$DISK" \
      --kubernetes-version="v$(pinned_version kubectl)"
    kubectl config use-context "$PROFILE" >/dev/null
    ;;
  status)
    minikube status -p "$PROFILE"
    kubectl --context "$PROFILE" get nodes
    ;;
  down)
    echo "Deleting Minikube profile '$PROFILE' and all its data"
    minikube delete -p "$PROFILE"
    ;;
  *)
    echo "Usage: $0 up|status|down" >&2
    exit 2
    ;;
esac
