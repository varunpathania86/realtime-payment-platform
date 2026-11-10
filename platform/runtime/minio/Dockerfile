ARG GO_VERSION
FROM golang:${GO_VERSION}-alpine AS download
ARG MINIO_SHA256
COPY minio /minio
RUN printf '%s  /minio\n' "${MINIO_SHA256}" | sha256sum -c - \
    && chmod 0555 /minio
FROM scratch
COPY --from=download /minio /minio
USER 10001:10001
EXPOSE 9000 9001
ENTRYPOINT ["/minio"]
