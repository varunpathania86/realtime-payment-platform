ARG FLINK_VERSION
FROM flink:${FLINK_VERSION}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /opt/job/requirements.txt
ARG FLINK_PYTHON_VERSION
ENV FLINK_PYTHON_VERSION="${FLINK_PYTHON_VERSION}"
RUN python3 -m venv /opt/pyflink && /opt/pyflink/bin/pip install --no-cache-dir -r /opt/job/requirements.txt
ARG FLINK_KAFKA_CONNECTOR_VERSION
ARG FLINK_KAFKA_CONNECTOR_SHA256
RUN set -eu; artifact="flink-sql-connector-kafka-${FLINK_KAFKA_CONNECTOR_VERSION}.jar"; \
    base="https://repo.maven.apache.org/maven2/org/apache/flink/flink-sql-connector-kafka/${FLINK_KAFKA_CONNECTOR_VERSION}"; \
    curl -fsSL "$base/$artifact" -o "/opt/flink/lib/$artifact"; \
    printf '%s  %s\n' "$FLINK_KAFKA_CONNECTOR_SHA256" "/opt/flink/lib/$artifact" | sha256sum -c -; \
    mkdir -p /opt/flink/plugins/s3-fs-hadoop; \
    cp /opt/flink/opt/flink-s3-fs-hadoop-*.jar /opt/flink/plugins/s3-fs-hadoop/
ENV PATH="/opt/pyflink/bin:${PATH}" \
    PYFLINK_CLIENT_EXECUTABLE="/opt/pyflink/bin/python" \
    PYFLINK_PYTHON="/opt/pyflink/bin/python"
COPY *.py /opt/job/
ARG VERSION=development
ARG COMMIT=unknown
LABEL org.opencontainers.image.version="${VERSION}" org.opencontainers.image.revision="${COMMIT}"
USER flink
