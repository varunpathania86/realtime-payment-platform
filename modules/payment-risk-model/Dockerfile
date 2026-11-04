ARG RISK_PYTHON_VERSION
FROM python:${RISK_PYTHON_VERSION}
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /opt/risk/requirements.txt
RUN pip install --no-cache-dir -r /opt/risk/requirements.txt
COPY *.py *.json /opt/risk/
WORKDIR /opt/risk
ENV OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
ARG VERSION=development
ARG COMMIT=unknown
LABEL org.opencontainers.image.version="${VERSION}" org.opencontainers.image.revision="${COMMIT}"
USER 1000:1000
ENTRYPOINT ["python", "/opt/risk/train.py"]
