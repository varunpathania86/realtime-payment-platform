# AI orchestrator API image. The ai-core build context supplies the shared AI lock and library.
ARG AI_PYTHON_VERSION=3.12.16-slim-bookworm
FROM python:${AI_PYTHON_VERSION}
COPY --from=ai-core requirements.txt /opt/ai-core/requirements.txt
RUN pip install --no-cache-dir --requirement /opt/ai-core/requirements.txt
COPY --from=ai-core src /opt/ai-core/src
COPY src /opt/ai-orchestrator/src
ENV PYTHONPATH=/opt/ai-orchestrator/src:/opt/ai-core/src \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANGSMITH_TRACING=false PORT=8080
ARG VERSION=development
ARG COMMIT=unknown
LABEL org.opencontainers.image.version="${VERSION}" org.opencontainers.image.revision="${COMMIT}"
USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"]
ENTRYPOINT ["python", "-m", "ai_orchestrator.api"]
