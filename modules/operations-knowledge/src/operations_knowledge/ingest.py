"""Index the bundled corpus: originals to MinIO, chunks and embeddings to PostgreSQL + pgvector.

Each document version is identified by a hash of its content and the chunking and embedding
versions, so reruns skip unchanged documents, changed documents get a new version and the
previous one is closed (`valid_to`), and documents removed from the manifest are retired.
Chunks that carry prompt-injection signals are stored but quarantined from retrieval.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

import httpx
import psycopg
from langchain_core.embeddings import Embeddings

from ai_core.embeddings import EMBEDDING_MODEL, EMBEDDING_VERSION, LlamaEmbeddings, vector_literal
from ai_core.retrieval import schema_sql
from ai_core.safety import injection_signals
from operations_knowledge.chunking import chunk_document, title_of
from operations_knowledge.manifest import Corpus, Source, load_corpus


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...


class MinioStore:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        from minio import Minio

        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)
        self._bucket = bucket
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(self._bucket, key, io.BytesIO(data), len(data), content_type="text/markdown")


def document_version(content: bytes, corpus: Corpus) -> str:
    identity = b"\0".join([content, corpus.chunking_version.encode(), EMBEDDING_VERSION.encode()])
    return hashlib.sha256(identity).hexdigest()[:16]


def _retire(connection: psycopg.Connection[Any], document_id: str) -> None:
    for table in ("knowledge_chunks", "knowledge_documents"):
        connection.execute(f"UPDATE {table} SET valid_to = now() WHERE document_id = %s AND valid_to IS NULL",
                            (document_id,))


def _index(
    connection: psycopg.Connection[Any], embeddings: Embeddings, store: ObjectStore, corpus: Corpus,
    source: Source, content: bytes, version: str,
) -> int:
    text = content.decode("utf-8")
    chunks = chunk_document(source.path, text)
    vectors = embeddings.embed_documents([f"{chunk.heading}\n\n{chunk.content}" for chunk in chunks])
    object_key = f"{source.path}@{version}"
    store.put(object_key, content)
    signals = [injection_signals(chunk.content) for chunk in chunks]
    content_hash = hashlib.sha256(content).hexdigest()
    with connection.transaction():
        _retire(connection, source.path)
        connection.execute(
            "INSERT INTO knowledge_documents (document_id, document_version, title, doc_type, tenant_id,"
            " classification, required_permission, source_uri, object_key, content_hash, quarantined)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (document_id, document_version) DO UPDATE SET valid_to = NULL, valid_from = now()",
            (source.path, version, title_of(text, source.path), source.doc_type, corpus.tenant,
            source.classification, source.required_permission, source.path, object_key, content_hash,
            any(signals)),
        )
        for index, (chunk, vector, found) in enumerate(zip(chunks, vectors, signals, strict=True)):
            chunk_id = hashlib.sha256(f"{source.path}\0{version}\0{index}".encode()).hexdigest()[:32]
            connection.execute(
                "INSERT INTO knowledge_chunks (chunk_id, document_id, document_version, chunk_index, heading,"
                " content, embedding, token_count, tenant_id, classification, required_permission, source_uri,"
                " content_hash, embedding_model, embedding_version, chunking_version, quarantined, metadata)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (chunk_id) DO UPDATE SET valid_to = NULL, valid_from = now()",
                (chunk_id, source.path, version, index, chunk.heading, chunk.content, vector_literal(vector),
                chunk.token_estimate, corpus.tenant, source.classification, source.required_permission,
                source.path, hashlib.sha256(chunk.content.encode()).hexdigest(), EMBEDDING_MODEL,
                EMBEDDING_VERSION, corpus.chunking_version, bool(found),
                json.dumps({"doc_type": source.doc_type, "injection_signals": found})),
            )
    return len(chunks)


def ingest(
    connection: psycopg.Connection[Any], embeddings: Embeddings, store: ObjectStore, root: Path
) -> dict[str, Any]:
    corpus = load_corpus(root)
    with connection.transaction():
        connection.execute(schema_sql())
    current = dict(connection.execute(
        "SELECT document_id, document_version FROM knowledge_documents WHERE valid_to IS NULL"
    ).fetchall())
    summary: dict[str, Any] = {"indexed": [], "unchanged": 0, "retired": [], "chunks": 0, "quarantined_chunks": 0}
    for source in corpus.sources:
        content = (root / source.path).read_bytes()
        version = document_version(content, corpus)
        if current.get(source.path) == version:
            summary["unchanged"] += 1
            continue
        summary["chunks"] += _index(connection, embeddings, store, corpus, source, content, version)
        summary["indexed"].append(source.path)
    for document_id in sorted(set(current) - {source.path for source in corpus.sources}):
        with connection.transaction():
            _retire(connection, document_id)
        summary["retired"].append(document_id)
    summary["quarantined_chunks"] = connection.execute(
        "SELECT count(*) FROM knowledge_chunks WHERE valid_to IS NULL AND quarantined"
    ).fetchone()[0]
    return summary


def wait_until_ready(url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if httpx.get(f"{url.rstrip('/')}/health", timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"embedding service at {url} was not ready within {timeout:.0f}s")
        time.sleep(5)


def main() -> None:
    embeddings_url = os.environ["LLM_EMBEDDINGS_URL"]
    wait_until_ready(embeddings_url, float(os.environ.get("LLM_READY_TIMEOUT_SECONDS", "900")))
    store = MinioStore(os.environ["MINIO_ENDPOINT"], os.environ["MINIO_ACCESS_KEY"], os.environ["MINIO_SECRET_KEY"],
                        os.environ.get("KNOWLEDGE_BUCKET", "knowledge-originals"))
    with tempfile.TemporaryDirectory() as directory:
        with tarfile.open(os.environ.get("CORPUS_ARCHIVE", "/corpus/corpus.tar.gz")) as archive:
            archive.extractall(directory, filter="data")
        with psycopg.connect(os.environ["KNOWLEDGE_DSN"], autocommit=True) as connection:
            summary = ingest(connection, LlamaEmbeddings(embeddings_url, timeout=120), store, Path(directory))
    print(json.dumps({"event": "knowledge_ingested", **summary}, sort_keys=True))


if __name__ == "__main__":
    main()
