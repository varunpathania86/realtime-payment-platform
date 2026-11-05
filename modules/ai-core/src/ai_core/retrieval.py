"""Authorized hybrid retrieval over PostgreSQL full-text search and exact pgvector similarity.

Permission, classification, tenant, version and quarantine filters run inside the SQL that
selects candidates, so a forbidden chunk is never returned and filtered afterwards. Rankings
are fused with Reciprocal Rank Fusion (k=60). The corpus is small, so vector search is exact.
Without an embedding (the embedding server is down) only the full-text ranking contributes.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any, Protocol, Sequence

from ai_core.embeddings import vector_literal
from ai_core.identity import Principal

RRF_K = 60
MAX_RESULTS = 8
# Each knowledge permission unlocks specific classifications; both checks run in SQL.
CLASSIFICATIONS = {
    "knowledge:read": ("public", "internal"),
    "knowledge:restricted": ("restricted",),
}

HYBRID_SQL = """
WITH allowed AS (
    SELECT * FROM knowledge_chunks
    WHERE valid_to IS NULL AND NOT quarantined
        AND required_permission = ANY(%(permissions)s)
        AND classification = ANY(%(classifications)s)
        AND tenant_id IN ('platform', %(tenant)s)
),
vector_rank AS (
    SELECT chunk_id, row_number() OVER (ORDER BY embedding <=> %(embedding)s::vector) AS rank
    FROM allowed WHERE %(embedding)s::vector IS NOT NULL
    ORDER BY embedding <=> %(embedding)s::vector LIMIT %(candidates)s
),
text_rank AS (
    SELECT chunk_id, row_number() OVER (
        ORDER BY ts_rank_cd(content_tsvector, websearch_to_tsquery('english', %(query)s)) DESC
    ) AS rank
    FROM allowed WHERE content_tsvector @@ websearch_to_tsquery('english', %(query)s)
    LIMIT %(candidates)s
),
fused AS (
    SELECT COALESCE(v.chunk_id, t.chunk_id) AS chunk_id,
            COALESCE(1.0 / (%(rrf_k)s + v.rank), 0) + COALESCE(1.0 / (%(rrf_k)s + t.rank), 0) AS score
    FROM vector_rank v FULL JOIN text_rank t ON v.chunk_id = t.chunk_id
)
SELECT c.chunk_id, c.document_id, c.document_version, c.heading, c.content, c.source_uri,
        c.classification, f.score::float8
FROM fused f JOIN allowed c ON c.chunk_id = f.chunk_id
ORDER BY f.score DESC, c.chunk_id
LIMIT %(limit)s
"""


class Cursor(Protocol):
    def execute(self, query: str, params: dict[str, Any]) -> Any: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_version: str
    heading: str
    content: str
    source_uri: str
    classification: str
    score: float


def schema_sql() -> str:
    return resources.files("ai_core").joinpath("sql/knowledge.sql").read_text(encoding="utf-8")


def readable_scope(principal: Principal) -> tuple[list[str], list[str]]:
    permissions = sorted(permission for permission in CLASSIFICATIONS if principal.has(permission))
    classifications = sorted({item for permission in permissions for item in CLASSIFICATIONS[permission]})
    return permissions, classifications


def hybrid_search(
    cursor: Cursor, principal: Principal, query: str, embedding: Sequence[float] | None, *, limit: int = 5,
    candidates: int = 20,
) -> list[RetrievedChunk]:
    permissions, classifications = readable_scope(principal)
    if not permissions:
        return []
    cursor.execute(HYBRID_SQL, {
        "permissions": permissions, "classifications": classifications, "tenant": principal.tenant,
        "embedding": None if embedding is None else vector_literal(embedding), "query": query[:500], "candidates": candidates,
        "rrf_k": RRF_K, "limit": max(1, min(limit, MAX_RESULTS)),
    })
    return [RetrievedChunk(*row) for row in cursor.fetchall()]
