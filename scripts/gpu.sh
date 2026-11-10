#!/usr/bin/env bash
# Shared opt-in GPU configuration for the single node or multi-cluster AI node.

gpu_enabled() {
  local settings="$REPO_ROOT/.local/$1/events.tfvars.json"
  [ -f "$settings" ] && jq -e '.ai_gpu_enabled == true' "$settings" >/dev/null
}

gpu_preflight() {
  local profile="$1" requests report
  command -v nvidia-smi >/dev/null ||
    { echo "NVIDIA driver is unavailable. Repair the host/Windows NVIDIA driver before enabling AI GPUs." >&2; return 1; }
  report=$(mktemp) || return 1
  # A driver stuck in uninterruptible sleep must not retain the caller's output pipes.
  if timeout -k 2 10 nvidia-smi --query-gpu=name,memory.total --format=csv,noheader >"$report" 2>&1; then
    cat "$report"
    rm -f "$report"
  else
    cat "$report" >&2
    rm -f "$report"
    echo "NVIDIA driver did not respond. Repair GPU access on the host (WSL may need a Windows driver update/restart)." >&2
    return 1
  fi
  docker info --format '{{json .Runtimes}}' | jq -e 'has("nvidia")' >/dev/null ||
    { echo "Docker NVIDIA runtime is missing; configure NVIDIA Container Toolkit on the Docker host first." >&2; return 1; }
  if docker container inspect "$profile" >/dev/null 2>&1; then
    requests=$(docker inspect "$profile" --format '{{json .HostConfig.DeviceRequests}}') || return 1
    printf '%s' "$requests" | jq -e 'any(.[]?; any(.Capabilities[]?[]?; . == "gpu"))' >/dev/null ||
      { echo "Existing profile $profile has no GPU passthrough. Preserve data/savepoints before explicitly recreating it; no automatic deletion is performed." >&2; return 1; }
  fi
}

gpu_prepare_chart() {
  local version destination
  version=$(pinned_version nvidia-device-plugin)
  destination="$REPO_ROOT/.local/tools/nvidia-device-plugin-$version.tgz"
  if [ -f "$destination" ] &&
    printf '%s  %s\n' "$(pinned_version nvidia-device-plugin-chart-sha256)" "$destination" | sha256sum --check --status; then
    return 0
  fi
  echo "GPU device-plugin chart is missing or its checksum differs. Run AI_GPU=1 make setup." >&2
  return 1
}
