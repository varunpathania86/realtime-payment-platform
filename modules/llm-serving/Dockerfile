# Pinned upstream llama.cpp server plus the checksum-verifying model fetch script used by the
# chart's init container. Models are not baked in; they download once into a persistent volume.
# Defaults match .tool-versions (checked by tests) so older publication tooling can still build.
ARG LLAMA_CPP_VERSION=server-b12124
ARG LLAMA_CPP_DIGEST=sha256:c0a11067c72bb1eb330b8bddbf9869274c195e596bf4fae55fd527102e644b85
FROM ghcr.io/ggml-org/llama.cpp:${LLAMA_CPP_VERSION}@${LLAMA_CPP_DIGEST}
COPY --chmod=0755 scripts/fetch-model.sh /opt/llm/fetch-model.sh
ARG VERSION=development
ARG COMMIT=unknown
LABEL org.opencontainers.image.version="${VERSION}" org.opencontainers.image.revision="${COMMIT}"
USER 65532:65532
