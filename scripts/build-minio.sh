#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# shellcheck source=scripts/github-release.sh
. "$(dirname "${BASH_SOURCE[0]}")/github-release.sh"
version=$(pinned_version minio)
fetch_github_asset minio/minio "$version" "minio.linux-${ARCH}.${version}" "$TMP/minio"
verify "$TMP/minio" "$(pinned_version "minio-sha256-$ARCH")"
cp "$REPO_ROOT/platform/runtime/minio/Dockerfile" "$TMP/Dockerfile"
docker build --build-arg "GO_VERSION=$(pinned_version go)" \
  --build-arg "MINIO_SHA256=$(pinned_version "minio-sha256-$ARCH")" \
  -t "rpp-runtime/minio:$version" "$TMP"
