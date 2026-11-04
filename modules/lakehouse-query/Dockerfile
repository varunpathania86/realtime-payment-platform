ARG TRINO_VERSION
FROM trinodb/trino:${TRINO_VERSION}
ARG VERSION=development
ARG COMMIT=unknown
LABEL org.opencontainers.image.version="${VERSION}" org.opencontainers.image.revision="${COMMIT}"
USER trino
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD /usr/lib/trino/bin/health-check || exit 1
