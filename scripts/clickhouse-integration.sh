#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$REPO_ROOT"

name="rpp-stage6-clickhouse-verify-$$"
image="rpp-local/realtime-analytics:verify-$$"
reader_password=$("$REPO_ROOT/.venv/bin/python" -c 'import secrets; print(secrets.token_hex(24))')
password=$("$REPO_ROOT/.venv/bin/python" -c 'import secrets; print(secrets.token_hex(32))')
container=""

cleanup() {
  local status=$?
  if [ "$status" -ne 0 ] && [ -n "$container" ]; then
    docker logs "$container" >&2 || true
  fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker image rm "$image" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

docker build \
  --build-arg "CLICKHOUSE_VERSION=$(pinned_version clickhouse)" \
  --build-arg VERSION=verification \
  --build-arg "COMMIT=$(git rev-parse HEAD)" \
  --tag "$image" modules/realtime-analytics
container=$(docker run --detach --name "$name" --memory=2g --cpus=2 \
  --read-only \
  --tmpfs /var/lib/clickhouse \
  --tmpfs /var/log/clickhouse-server \
  --tmpfs /tmp \
  --tmpfs /etc/clickhouse-server/users.d \
  --publish 127.0.0.1::8123 \
  --env CLICKHOUSE_DB=analytics \
  --env CLICKHOUSE_USER=rpp_analytics \
  --env "CLICKHOUSE_PASSWORD=$password" \
  --env CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  --mount "type=bind,source=$REPO_ROOT/modules/realtime-analytics/chart/files/schema.sql,target=/tmp/schema.sql,readonly" \
  --mount "type=bind,source=$REPO_ROOT/modules/realtime-analytics/chart/files/bi-reader.sh,target=/tmp/bi-reader.sh,readonly" \
  "$image")
port=$(docker port "$container" 8123/tcp | sed 's/.*://')
ready=false
for ((attempt = 0; attempt < 90; attempt++)); do
  if curl -fsS "http://127.0.0.1:$port/ping" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
[ "$ready" = true ] || {
  echo "ClickHouse did not become ready" >&2
  if [ "$(docker inspect --format '{{.State.Running}}' "$container")" = true ]; then
    docker exec "$container" sh -c 'tail -n 100 /var/log/clickhouse-server/clickhouse-server.log /var/log/clickhouse-server/clickhouse-server.err.log' >&2 || true
  fi
  exit 1
}
docker exec "$container" clickhouse-client --user rpp_analytics --password "$password" \
  --multiquery --queries-file /tmp/schema.sql
docker exec --env CLICKHOUSE_HOST=127.0.0.1 --env CLICKHOUSE_USER=rpp_analytics \
  --env "CLICKHOUSE_PASSWORD=$password" --env BI_READER_USER=rpp_bi_reader \
  --env BI_READER_DATABASE=analytics --env "BI_READER_PASSWORD=$reader_password" \
  "$container" sh /tmp/bi-reader.sh
CLICKHOUSE_PASSWORD="$password" BI_READER_PASSWORD="$reader_password" "$REPO_ROOT/.venv/bin/python" \
  modules/realtime-analytics/tests/runtime_check.py --url "http://127.0.0.1:$port"
echo "ClickHouse schema, inserts, deduplication, revisions and the SELECT-only BI reader passed."
