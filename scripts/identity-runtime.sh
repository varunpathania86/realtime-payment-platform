#!/usr/bin/env bash
# Build the optimized identity runtime from the checksum-pinned upstream image.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

image() {
  local digest
  digest=$(
    {
      pinned_version keycloak
      pinned_version keycloak-image-digest
      cd "$REPO_ROOT/platform/runtime/identity-provider"
      sha256sum Dockerfile
    } | sha256sum | cut -c1-16
  )
  printf 'rpp-local/identity-provider:dev-%s\n' "$digest"
}

build() {
  if docker image inspect "$(image)" >/dev/null 2>&1; then
    return
  fi
  docker build \
    --build-arg "KEYCLOAK_VERSION=$(pinned_version keycloak)" \
    --build-arg "KEYCLOAK_DIGEST=$(pinned_version keycloak-image-digest)" \
    -t "$(image)" "$REPO_ROOT/platform/runtime/identity-provider"
}

case "${1:-}" in
  image) image ;;
  build) build ;;
  setup) [ "${IDENTITY:-0}" != 1 ] || build ;;
  *) echo "Usage: identity-runtime.sh {image|build|setup}" >&2; exit 1 ;;
esac
