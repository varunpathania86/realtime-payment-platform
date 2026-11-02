#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

version=$(pinned_version cnpg-chart)
checksum=$(pinned_version cnpg-chart-sha256)
destination="$REPO_ROOT/.local/tools/cloudnative-pg-$version.tgz"
mkdir -p "$REPO_ROOT/.local/tools"
if [ -f "$destination" ] && printf '%s  %s\n' "$checksum" "$destination" | sha256sum --check --status; then
  exit 0
fi
temporary=$(mktemp "$REPO_ROOT/.local/tools/cnpg.XXXXXX")
trap 'rm -f "$temporary"' EXIT
curl -fsSL --retry 3 "https://github.com/cloudnative-pg/charts/releases/download/cloudnative-pg-v$version/cloudnative-pg-$version.tgz" -o "$temporary"
printf '%s  %s\n' "$checksum" "$temporary" | sha256sum --check --status ||
  { echo "CNPG chart checksum mismatch; restore .tool-versions or retry make setup" >&2; exit 1; }
mv "$temporary" "$destination"
