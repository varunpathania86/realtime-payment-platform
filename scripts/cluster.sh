#!/usr/bin/env bash
# Manage the local Minikube cluster. Usage: scripts/cluster.sh up|status|down
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
export PATH="$BIN_DIR:$PATH"
# shellcheck source=scripts/gpu.sh
. "$(dirname "${BASH_SOURCE[0]}")/gpu.sh"

PROFILE="${MINIKUBE_PROFILE:-rpp}"
CPUS="${MINIKUBE_CPUS:-4}"
MEMORY="${MINIKUBE_MEMORY:-8192}"
DISK="${MINIKUBE_DISK:-40g}"

case "${1:-}" in
  up)
    refuse_fenced_profile "$PROFILE"
    docker info >/dev/null 2>&1 || { echo "Docker is not reachable: see docs/developer-setup.md" >&2; exit 1; }
    options=()
    if gpu_enabled "$PROFILE"; then
      gpu_prepare_chart
      gpu_preflight "$PROFILE"
      options+=(--container-runtime=docker --gpus=all)
    fi
    minikube start -p "$PROFILE" --driver=docker --cpus="$CPUS" --memory="$MEMORY" --disk-size="$DISK" \
      --kubernetes-version="v$(pinned_version kubectl)" "${options[@]}"
    if gpu_enabled "$PROFILE"; then
      # Terraform installs the configured sharing plugin; avoid competing registrations.
      minikube -p "$PROFILE" addons disable nvidia-device-plugin
    fi
    kubectl config use-context "$PROFILE" >/dev/null
    ;;
  status)
    minikube status -p "$PROFILE"
    kubectl --context "$PROFILE" get nodes
    ;;
  down)
    refuse_fenced_profile "$PROFILE"
    echo "Deleting Minikube profile '$PROFILE' and all its data"
    minikube delete -p "$PROFILE"
    ;;
  *)
    echo "Usage: $0 up|status|down" >&2
    exit 2
    ;;
esac
