# Batch knowledge ingestion image. The ai-core build context supplies the shared AI lock and library.
ARG AI_PYTHON_VERSION=3.12.16-slim-bookworm
FROM python:${AI_PYTHON_VERSION}
COPY --from=ai-core requirements.txt /opt/ai-core/requirements.txt
RUN pip install --no-cache-dir --requirement /opt/ai-core/requirements.txt
COPY --from=ai-core src /opt/ai-core/src
COPY src /opt/operations-knowledge/src
ENV PYTHONPATH=/opt/operations-knowledge/src:/opt/ai-core/src \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANGSMITH_TRACING=false
ARG VERSION=development
ARG COMMIT=unknown
LABEL org.opencontainers.image.version="${VERSION}" org.opencontainers.image.revision="${COMMIT}"
USER 65532:65532
ENTRYPOINT ["python", "-m", "operations_knowledge.ingest"]
