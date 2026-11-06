#!/usr/bin/env bash
# Pinned Python runtime for the AI modules' offline checks.
#   ai-python.sh setup              build the runtime when AI=1 (called by make setup)
#   ai-python.sh image              print the runtime image reference
#   ai-python.sh run MODULE CMD...  run CMD in modules/MODULE with its catalog dependencies on PYTHONPATH
#   ai-python.sh pgvector MODULE CMD...  like run, beside a throwaway pinned pgvector database
#                                   (KNOWLEDGE_ADMIN_DSN points at it; nothing is published on the host)
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

image() {
  local lock
  lock=$(sha256sum "$REPO_ROOT/modules/ai-core/requirements.txt" | cut -c1-12)
  printf 'rpp-tools/ai-python:%s-%s\n' "$(pinned_version ai-python)" "$lock"
}

build() {
  docker image inspect "$(image)" >/dev/null 2>&1 && return 0
  echo "Building $(image) (first use downloads the pinned Python packages)" >&2
  docker build --build-arg "AI_PYTHON_VERSION=$(pinned_version ai-python)" \
    -f "$REPO_ROOT/modules/ai-core/runtime.Dockerfile" -t "$(image)" "$REPO_ROOT/modules/ai-core" >&2
}

run() {
  local module="$1" path dependency python="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
  shift
  [ -f "$REPO_ROOT/modules/$module/module.yaml" ] || { echo "unknown module: $module" >&2; exit 1; }
  path="/opt/modules/$module/src:/opt/modules/$module"
  while IFS= read -r dependency; do
    [ -n "$dependency" ] && path="$path:/opt/modules/$dependency/src"
  done < <("$python" "$REPO_ROOT/scripts/module_catalog.py" dependencies --name "$module")
  build
  docker run --rm --network "${NETWORK:-none}" "${EXTRA_ENV[@]}" --user "$(id -u):$(id -g)" \
    -e HOME=/tmp -e "PYTHONPATH=$path" \
    -v "$REPO_ROOT/modules:/opt/modules:ro" -w "/opt/modules/$module" "$(image)" "$@"
}

with_pgvector() {
  local id password ready=false
  id="rpp-ai-check-$$"
  password=$(od -An -tx1 -N12 /dev/urandom | tr -d ' \n')
  build
  docker network create "$id" >/dev/null
  # Expand now: the local id is out of scope when the EXIT trap fires.
  # shellcheck disable=SC2064
  trap "docker rm -f '$id-pg' >/dev/null 2>&1; docker network rm '$id' >/dev/null 2>&1" EXIT
  docker run -d --name "$id-pg" --network "$id" -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=knowledge \
    "pgvector/pgvector:$(pinned_version pgvector)" >/dev/null
  # The image's init server listens only on a socket; TCP readiness means initialization finished.
  for _ in $(seq 1 60); do
    if docker exec "$id-pg" pg_isready -q -h 127.0.0.1 -U postgres -d knowledge; then ready=true; break; fi
    sleep 1
  done
  [ "$ready" = true ] || { docker logs "$id-pg" >&2; echo "pgvector did not become ready" >&2; exit 1; }
  NETWORK="$id"
  EXTRA_ENV=(-e "KNOWLEDGE_ADMIN_DSN=postgresql://postgres:$password@$id-pg:5432/knowledge")
  run "$@"
}

EXTRA_ENV=()
case "${1:-}" in
  setup) if [ "${AI:-0}" = 1 ]; then build; fi ;;
  image) image ;;
  build) build ;;
  run) shift; run "$@" ;;
  pgvector) shift; with_pgvector "$@" ;;
  *) echo "usage: $0 setup|image|build|run|pgvector MODULE COMMAND..." >&2; exit 2 ;;
esac
