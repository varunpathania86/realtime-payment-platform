#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Optional runtime preparation uses the same Dockerfile as local/publication builds.
[ "${STREAMING:-0}" = 1 ] || exit 0
docker build --build-arg "FLINK_VERSION=$(pinned_version flink)" \
  --build-arg "FLINK_PYTHON_VERSION=$(pinned_version flink-python)" \
  --build-arg "FLINK_KAFKA_CONNECTOR_VERSION=$(pinned_version flink-kafka-connector)" \
  --build-arg "FLINK_KAFKA_CONNECTOR_SHA256=$(pinned_version flink-kafka-connector-sha256)" \
  --build-arg "ICEBERG_VERSION=$(pinned_version iceberg)" \
  --build-arg "ICEBERG_FLINK_RUNTIME_SHA256=$(pinned_version iceberg-flink-runtime-sha256)" \
  --build-arg "ICEBERG_AWS_BUNDLE_SHA256=$(pinned_version iceberg-aws-bundle-sha256)" \
  --build-arg "HADOOP_CLIENT_VERSION=$(pinned_version hadoop-client)" \
  --build-arg "HADOOP_CLIENT_API_SHA256=$(pinned_version hadoop-client-api-sha256)" \
  --build-arg "HADOOP_CLIENT_RUNTIME_SHA256=$(pinned_version hadoop-client-runtime-sha256)" \
  -t "rpp-tools/flink:$(pinned_version flink-python)" "$REPO_ROOT/modules/flink-jobs"
bash "$REPO_ROOT/scripts/build-minio.sh"
