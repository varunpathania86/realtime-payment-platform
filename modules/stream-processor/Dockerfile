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
# Iceberg's Flink runtime needs shaded Hadoop classes even with the REST catalog and S3FileIO.
ARG ICEBERG_VERSION
ARG ICEBERG_FLINK_RUNTIME_SHA256
ARG ICEBERG_AWS_BUNDLE_SHA256
ARG HADOOP_CLIENT_VERSION
ARG HADOOP_CLIENT_API_SHA256
ARG HADOOP_CLIENT_RUNTIME_SHA256
RUN set -eu; maven="https://repo.maven.apache.org/maven2/org/apache"; \
    flink_minor="$(echo "$FLINK_PYTHON_VERSION" | cut -d. -f1,2)"; \
    fetch() { curl -fsSL "$maven/$1/$2" -o "/opt/flink/lib/$2"; \
      printf '%s  %s\n' "$3" "/opt/flink/lib/$2" | sha256sum -c -; }; \
    fetch "iceberg/iceberg-flink-runtime-$flink_minor/$ICEBERG_VERSION" \
      "iceberg-flink-runtime-$flink_minor-$ICEBERG_VERSION.jar" "$ICEBERG_FLINK_RUNTIME_SHA256"; \
    fetch "iceberg/iceberg-aws-bundle/$ICEBERG_VERSION" "iceberg-aws-bundle-$ICEBERG_VERSION.jar" \
      "$ICEBERG_AWS_BUNDLE_SHA256"; \
    fetch "hadoop/hadoop-client-api/$HADOOP_CLIENT_VERSION" "hadoop-client-api-$HADOOP_CLIENT_VERSION.jar" \
      "$HADOOP_CLIENT_API_SHA256"; \
    fetch "hadoop/hadoop-client-runtime/$HADOOP_CLIENT_VERSION" "hadoop-client-runtime-$HADOOP_CLIENT_VERSION.jar" \
      "$HADOOP_CLIENT_RUNTIME_SHA256"
ENV PATH="/opt/pyflink/bin:${PATH}" \
    PYFLINK_CLIENT_EXECUTABLE="/opt/pyflink/bin/python" \
    PYFLINK_PYTHON="/opt/pyflink/bin/python"
COPY *.py /opt/job/
ARG VERSION=development
ARG COMMIT=unknown
LABEL org.opencontainers.image.version="${VERSION}" org.opencontainers.image.revision="${COMMIT}"
USER flink
