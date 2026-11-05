#!/usr/bin/env bash
# Verify the developer environment against .tool-versions. Exit 1 if anything is missing or wrong.
set -uo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

fail=0
ok()   { printf 'ok       %s\n' "$*"; }
bad()  { printf 'PROBLEM  %s\n' "$*"; fail=1; }

for t in git make python3 curl; do
  if command -v "$t" >/dev/null; then ok "$t"; else bad "$t is missing (see docs/developer-setup.md)"; fi
done

if [ -x "$REPO_ROOT/.venv/bin/pre-commit" ] || command -v pre-commit >/dev/null; then
  ok pre-commit
else
  bad "pre-commit is missing (run: make setup)"
fi

if [ -x "$REPO_ROOT/.venv/bin/python" ] &&
  "$REPO_ROOT/.venv/bin/python" -c 'import avro.schema, jsonschema, yaml' >/dev/null 2>&1; then
  ok "catalog validation dependencies"
else
  bad "catalog validation dependencies are missing (run: make setup)"
fi

for t in kubectl minikube helm terraform go jq yq nodejs tflint trivy; do
  want=$(pinned_version "$t"); have=$(installed_version "$t")
  if [ -z "$have" ]; then bad "$t is missing, want $want (run: make setup)"
  elif [ "$have" != "$want" ]; then bad "$t is $have, want $want (run: make setup)"
  else ok "$t $have"; fi
done

if PATH="$BIN_DIR:$PATH" npm --version >/dev/null 2>&1; then
  ok npm
else
  bad "npm is missing (run: make setup)"
fi

if [ -z "$(installed_version docker)" ]; then
  bad "docker is missing (run: make setup)"
elif ! docker info >/dev/null 2>&1; then
  if id -nG "$USER" | grep -qw docker; then
    bad "docker daemon is not reachable (run: make docker, or start Docker Desktop)"
  else
    bad "docker daemon is not reachable (run: make docker, or start Docker Desktop)"
  fi
else
  ok "docker $(installed_version docker)"
fi

if [[ "$(pinned_version postgres)" =~ ^[0-9]+\.[0-9]+-alpine$ ]]; then
  ok "PostgreSQL container pin $(pinned_version postgres) (pulled by local deployment/integration tests)"
else
  bad "PostgreSQL container pin is missing/invalid in .tool-versions; restore it before make dev-up or module integration"
fi
if [[ "$(pinned_version clickhouse)" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+-alpine$ ]]; then
  ok "ClickHouse container pin $(pinned_version clickhouse) (built by local deployment)"
else
  bad "ClickHouse container pin is missing/invalid in .tool-versions; restore it before analytics development"
fi

for runtime in kafka apicurio otelcol prometheus alertmanager loki tempo grafana; do
  if [[ "$(pinned_version "$runtime")" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    ok "$runtime container pin $(pinned_version "$runtime") (pulled by local deployment/integration tests)"
  else
    bad "$runtime container pin is missing/invalid in .tool-versions; restore it before make dev-up or module integration"
  fi
done

if [ "${MULTI_CLUSTER:-0}" = 1 ]; then
  if [ -r /proc/sys/fs/inotify/max_user_instances ] &&
    [ "$(cat /proc/sys/fs/inotify/max_user_instances)" -ge 1024 ]; then
    ok "multi-cluster host inotify instance limit"
  else
    bad "multi-cluster needs fs.inotify.max_user_instances >= 1024 on the Docker host (run: MULTI_CLUSTER=1 make setup)"
  fi
fi

cnpg_chart="$REPO_ROOT/.local/tools/cloudnative-pg-$(pinned_version cnpg-chart).tgz"
if [ -f "$cnpg_chart" ] &&
  printf '%s  %s\n' "$(pinned_version cnpg-chart-sha256)" "$cnpg_chart" | sha256sum --check --status; then
  ok "CNPG operator chart $(pinned_version cnpg-chart) (checksum verified)"
else
  bad "CNPG chart is missing or checksum differs (run: make setup)"
fi

if [[ "$(pinned_version flink)" =~ ^[0-9]+\.[0-9]+\.[0-9]+-scala_2.12-java17$ ]] &&
  [[ "$(pinned_version flink-kafka-connector)" =~ ^[0-9]+\.[0-9]+\.[0-9]+-1.20$ ]] &&
  [[ "$(pinned_version flink-kafka-connector-sha256)" =~ ^[a-f0-9]{64}$ ]] &&
  [[ "$(pinned_version minio-sha256-amd64)" =~ ^[a-f0-9]{64}$ ]] &&
  [[ "$(pinned_version minio-sha256-arm64)" =~ ^[a-f0-9]{64}$ ]] &&
  [[ "$(pinned_version minio)" =~ ^RELEASE\.[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}Z$ ]]; then
  ok "Flink/PyFlink, Kafka connector and MinIO container pins"
else
  bad "invalid streaming runtime pins in .tool-versions (restore pins and run STREAMING=1 make setup)"
fi
if [[ "$(pinned_version polaris)" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] &&
  [[ "$(pinned_version trino)" =~ ^[0-9]+$ ]] &&
  [[ "$(pinned_version iceberg)" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] &&
  [[ "$(pinned_version hadoop-client)" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] &&
  [[ "$(pinned_version iceberg-flink-runtime-sha256)" =~ ^[a-f0-9]{64}$ ]] &&
  [[ "$(pinned_version iceberg-aws-bundle-sha256)" =~ ^[a-f0-9]{64}$ ]] &&
  [[ "$(pinned_version hadoop-client-api-sha256)" =~ ^[a-f0-9]{64}$ ]] &&
  [[ "$(pinned_version hadoop-client-runtime-sha256)" =~ ^[a-f0-9]{64}$ ]]; then
  ok "Polaris $(pinned_version polaris), Trino $(pinned_version trino) and Iceberg $(pinned_version iceberg) lakehouse pins (built by local deployment)"
else
  bad "invalid lakehouse pins in .tool-versions (restore polaris, trino, iceberg and hadoop-client pins/checksums)"
fi
if [[ "$(pinned_version superset)" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] &&
  [[ "$(pinned_version superset-image-digest)" =~ ^sha256:[a-f0-9]{64}$ ]]; then
  ok "Superset $(pinned_version superset) BI image pin (built by local deployment)"
else
  bad "invalid Superset pins in .tool-versions (restore superset and superset-image-digest)"
fi
if [[ "$(pinned_version ai-python)" =~ ^3\.[0-9]+\.[0-9]+-slim-bookworm$ ]] &&
  [[ "$(pinned_version llama-cpp)" =~ ^server-b[0-9]+$ ]] &&
  [[ "$(pinned_version llama-cpp-image-digest)" =~ ^sha256:[a-f0-9]{64}$ ]] &&
  [[ "$(pinned_version pgvector)" =~ ^[0-9]+\.[0-9]+\.[0-9]+-pg16-bookworm$ ]]; then
  ok "AI assistant image pins: Python $(pinned_version ai-python), llama.cpp $(pinned_version llama-cpp), pgvector $(pinned_version pgvector) (built or pulled by local deployment)"
else
  bad "invalid AI pins in .tool-versions (restore ai-python, llama-cpp, llama-cpp-image-digest and pgvector)"
fi
flink_pin=$(pinned_version flink)
if [ "${flink_pin%%-*}" != "$(pinned_version flink-python)" ]; then
  bad "Flink and PyFlink pins must match (restore .tool-versions and run STREAMING=1 make setup)"
fi
if [ "${STREAMING:-0}" = 1 ]; then
  if docker image inspect "rpp-tools/flink:$(pinned_version flink-python)" >/dev/null 2>&1 &&
    docker image inspect "rpp-runtime/minio:$(pinned_version minio)" >/dev/null 2>&1; then
    ok "streaming runtime images (run STREAMING=1 make setup to refresh)"
  else
    bad "streaming runtime images are missing (run STREAMING=1 make setup)"
  fi
fi

if [ "${AI:-0}" = 1 ]; then
  if docker image inspect "$(bash "$REPO_ROOT/scripts/ai-python.sh" image)" >/dev/null 2>&1; then
    ok "AI Python test runtime $(bash "$REPO_ROOT/scripts/ai-python.sh" image)"
  else
    bad "AI Python test runtime missing (run: AI=1 make setup)"
  fi
fi

if [ "${RISK_MODEL:-0}" = 1 ]; then
  if docker image inspect "rpp-tools/payment-risk:$(pinned_version risk-python)" >/dev/null 2>&1; then
    ok "risk training runtime $(pinned_version risk-python)"
  else
    bad "risk training runtime missing (run: RISK_MODEL=1 make setup)"
  fi
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) bad "$BIN_DIR is not on PATH (add it in ~/.bashrc)" ;;
esac

exit $fail
