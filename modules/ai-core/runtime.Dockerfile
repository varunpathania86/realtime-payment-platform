# Offline test runtime for the Python AI modules, built by scripts/ai-python.sh.
# It is not a deployable image: deployable AI modules install the same lock in their own Dockerfile.
ARG AI_PYTHON_VERSION=3.12.16-slim-bookworm
FROM python:${AI_PYTHON_VERSION}
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --root-user-action=ignore -r /tmp/requirements.txt && rm /tmp/requirements.txt
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANGSMITH_TRACING=false
