"""LangChain embeddings adapter for the llama.cpp OpenAI-compatible embeddings endpoint."""

from __future__ import annotations

import math
from collections.abc import Sequence

import httpx
from langchain_core.embeddings import Embeddings

EMBEDDING_MODEL = "nomic-embed-text-v1.5"
EMBEDDING_VERSION = "f16-970aa74c0a90"
DIMENSIONS = 768
# nomic-embed-text is trained with task prefixes; documents and queries must use different ones.
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class EmbeddingError(Exception):
    """The embedding service failed or returned an unexpected vector."""


class LlamaEmbeddings(Embeddings):
    def __init__(
        self, base_url: str, *, timeout: float = 30.0, batch_size: int = 16,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)
        self._batch_size = batch_size

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start:start + self._batch_size])
            try:
                response = self._client.post("/v1/embeddings", json={"model": EMBEDDING_MODEL, "input": batch})
                response.raise_for_status()
                data = sorted(response.json()["data"], key=lambda item: item["index"])
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
                raise EmbeddingError("embedding request failed") from error
            if len(data) != len(batch):
                raise EmbeddingError("embedding count does not match the input count")
            for item in data:
                vector = [float(value) for value in item["embedding"]]
                if len(vector) != DIMENSIONS or not all(math.isfinite(value) for value in vector):
                    raise EmbeddingError(f"expected {DIMENSIONS} finite dimensions")
                vectors.append(vector)
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([DOCUMENT_PREFIX + text for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([QUERY_PREFIX + text])[0]


def vector_literal(vector: Sequence[float]) -> str:
    """Render a pgvector text literal without needing the pgvector client package."""
    return "[" + ",".join(format(float(value), ".7g") for value in vector) + "]"
