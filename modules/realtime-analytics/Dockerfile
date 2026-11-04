ARG CLICKHOUSE_VERSION
FROM clickhouse/clickhouse-server:${CLICKHOUSE_VERSION}
ARG VERSION=development
ARG COMMIT=unknown
LABEL org.opencontainers.image.version="${VERSION}" org.opencontainers.image.revision="${COMMIT}"
USER clickhouse
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD clickhouse-client --query 'SELECT 1' || exit 1
