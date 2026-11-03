ARG GO_VERSION
FROM golang:${GO_VERSION}-alpine AS download
ARG TARGETARCH
ARG MINIO_VERSION
ARG MINIO_SHA256
RUN wget -q "https://github.com/minio/minio/releases/download/${MINIO_VERSION}/minio.linux-${TARGETARCH}.${MINIO_VERSION}" -O /minio \
    && printf '%s  /minio\n' "${MINIO_SHA256}" | sha256sum -c - \
    && chmod 0555 /minio
FROM scratch
COPY --from=download /minio /minio
USER 10001:10001
EXPOSE 9000 9001
ENTRYPOINT ["/minio"]
