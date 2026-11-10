#!/usr/bin/env bash
# Package checksum-pinned server-side drivers for DBX's supported offline importer.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

image() {
  local digest
  digest=$(
    {
      grep '^dbx' "$REPO_ROOT/.tool-versions"
      cd "$REPO_ROOT"
      sha256sum platform/runtime/dbx/Dockerfile scripts/dbx_runtime_package.py
    } | sha256sum | cut -c1-16
  )
  printf 'rpp-local/dbx-workbench:dev-%s\n' "$digest"
}

build() {
  if docker image inspect "$(image)" >/dev/null 2>&1; then
    return
  fi
  local arch package
  arch=$(docker version --format '{{.Server.Arch}}')
  package=$("$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/dbx_runtime_package.py" "$arch")
  cp "$REPO_ROOT/platform/runtime/dbx/Dockerfile" "$(dirname "$package")/Dockerfile"
  docker build --build-arg "DBX_VERSION=$(pinned_version dbx)" \
    --build-arg "DBX_DIGEST=$(pinned_version dbx-image-digest)" \
    -t "$(image)" "$(dirname "$package")"
}

case "${1:-}" in
  image) image ;;
  build) build ;;
  setup) [ "${DBX:-0}" != 1 ] || build ;;
  *) echo "Usage: dbx-runtime.sh {image|build|setup}" >&2; exit 1 ;;
esac
