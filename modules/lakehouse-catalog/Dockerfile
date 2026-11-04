ARG POLARIS_VERSION
FROM apache/polaris-admin-tool:${POLARIS_VERSION} AS admin

FROM apache/polaris:${POLARIS_VERSION}
ARG VERSION=development
ARG COMMIT=unknown
LABEL org.opencontainers.image.version="${VERSION}" org.opencontainers.image.revision="${COMMIT}"
# One image keeps every chart container on the module's own verified digest.
COPY --from=admin --chown=root:root /deployments /opt/polaris-admin
COPY --chown=root:root setup_catalog.py /opt/rpp/setup_catalog.py
USER 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD curl -fsS http://localhost:8182/q/health/ready || exit 1
