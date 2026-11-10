#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
case "${AI_GPU:-0}" in
  0) exit 0 ;;
  1) ;;
  *) echo "AI_GPU must be 0 or 1" >&2; exit 1 ;;
esac
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# shellcheck source=scripts/github-release.sh
. "$(dirname "${BASH_SOURCE[0]}")/github-release.sh"
version=$(pinned_version nvidia-device-plugin)
destination="$REPO_ROOT/.local/tools/nvidia-device-plugin-$version.tgz"
checksum=$(pinned_version nvidia-device-plugin-chart-sha256)
if [ -f "$destination" ] && printf '%s  %s\n' "$checksum" "$destination" | sha256sum --check --status; then
  exit 0
fi
mkdir -p "$REPO_ROOT/.local/tools"
fetch_github_asset NVIDIA/k8s-device-plugin "v$version" "nvidia-device-plugin-$version.tgz" "$TMP/plugin.tgz"
verify "$TMP/plugin.tgz" "$checksum"
install -m 0644 "$TMP/plugin.tgz" "$destination"
