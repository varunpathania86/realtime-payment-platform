# Defaults match .tool-versions (checked by tests) so older publication tooling can still build.
ARG SUPERSET_VERSION=6.1.0
ARG SUPERSET_DIGEST=sha256:16b50bbef6648912a79e3293d418fefa743ce555d36c5af6b85d859119ed7f88
FROM apache/superset:${SUPERSET_VERSION}@${SUPERSET_DIGEST}
ARG VERSION=development
ARG COMMIT=unknown
LABEL org.opencontainers.image.version="${VERSION}" org.opencontainers.image.revision="${COMMIT}"
USER root
COPY requirements.txt /app/rpp/requirements.txt
# Freezing Superset's installed packages as constraints keeps the drivers from upgrading them.
RUN uv pip freeze --python /app/.venv/bin/python | grep -v 'file://' > /tmp/superset-constraints.txt \
  && uv pip install --python /app/.venv/bin/python --no-cache \
    --constraint /tmp/superset-constraints.txt --requirement /app/rpp/requirements.txt \
  && rm /tmp/superset-constraints.txt
# /app/pythonpath is on PYTHONPATH, so Superset loads superset_config.py automatically.
COPY superset/ /app/pythonpath/
USER superset
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8088/health || exit 1
