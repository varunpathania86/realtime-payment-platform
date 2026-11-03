#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
docker build --build-arg "GO_VERSION=$(pinned_version go)" \
  --build-arg "MINIO_VERSION=$(pinned_version minio)" \
  --build-arg "MINIO_SHA256=$(pinned_version "minio-sha256-$ARCH")" \
  -t "rpp-runtime/minio:$(pinned_version minio)" "$REPO_ROOT/platform/runtime/minio"
