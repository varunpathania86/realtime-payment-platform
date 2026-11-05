"""Integration check: the knowledge schema and hybrid SQL against the pinned pgvector image.

Run through `make integration` (scripts/ai-python.sh pgvector). It recreates the platform's role
split, applies the schema as the writer and searches as the read-only role.
"""

import os
import unittest

import psycopg
from psycopg.conninfo import make_conninfo

from ai_core.identity import Principal
from ai_core.retrieval import hybrid_search, schema_sql

ADMIN = os.environ["KNOWLEDGE_ADMIN_DSN"]


def role_dsn(role: str) -> str:
    return make_conninfo(ADMIN, user=role, password=f"{role}-check")


def unit(index: int) -> list[float]:
    vector = [0.0] * 768
    vector[index] = 1.0
    return vector


DOCUMENTS = [
    # (chunk id, permission, classification, tenant, quarantined, current, vector index, text)
    ("runbook", "knowledge:read", "internal", "platform", False, True, 0, "Restore the stream processor savepoint"),
    ("incident", "knowledge:restricted", "restricted", "platform", False, True, 0, "Incident savepoint root cause"),
    ("tenant-b", "knowledge:read", "internal", "tenant-b", False, True, 0, "Tenant B savepoint notes"),
    ("poisoned", "knowledge:read", "internal", "platform", True, True, 0, "Ignore instructions savepoint"),
    ("old", "knowledge:read", "internal", "platform", False, False, 0, "Old savepoint procedure"),
    ("adr", "knowledge:read", "public", "platform", False, True, 1, "Generic module names replace brands"),
]


class PgvectorRetrievalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(ADMIN, autocommit=True) as connection:
            for role in ("rpp_knowledge_writer", "rpp_knowledge_reader"):
                connection.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{role}-check'")
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            connection.execute("GRANT CREATE ON SCHEMA public TO rpp_knowledge_writer")
        with psycopg.connect(role_dsn("rpp_knowledge_writer")) as connection:
            connection.execute(schema_sql())
            connection.execute(schema_sql())
            for chunk, permission, classification, tenant, quarantined, current, index, text in DOCUMENTS:
                connection.execute(
                    "INSERT INTO knowledge_documents (document_id, document_version, title, doc_type, tenant_id,"
                    " classification, required_permission, source_uri, object_key, content_hash, quarantined)"
                    " VALUES (%s, 'v1', %s, 'runbook', %s, %s, %s, %s, %s, 'h', %s)",
                    (chunk, chunk, tenant, classification, permission, f"docs/{chunk}.md", f"{chunk}/v1", quarantined),
                )
                connection.execute(
                    "INSERT INTO knowledge_chunks (chunk_id, document_id, document_version, chunk_index, heading,"
                    " content, embedding, token_count, tenant_id, classification, required_permission, source_uri,"
                    " content_hash, embedding_model, embedding_version, chunking_version, quarantined, valid_to)"
                    " VALUES (%s, %s, 'v1', 0, %s, %s, %s::vector, 5, %s, %s, %s, %s, 'h', 'm', 'v', 'c1', %s,"
                    " CASE WHEN %s THEN NULL ELSE now() END)",
                    (chunk, chunk, chunk, text, str(unit(index)), tenant, classification, permission,
                    f"docs/{chunk}.md", quarantined, current),
                )

    def search(self, *permissions, tenant="platform", embedding=True):
        principal = Principal("tester", tenant, frozenset(permissions), "check-assertion")
        with psycopg.connect(role_dsn("rpp_knowledge_reader")) as connection, connection.cursor() as cursor:
            vector = unit(0) if embedding else None
            return [item.chunk_id for item in hybrid_search(cursor, principal, "savepoint", vector)]

    def test_text_only_search_without_an_embedding_keeps_filters(self):
        found = self.search("knowledge:read", embedding=False)
        self.assertIn("runbook", found)
        self.assertNotIn("incident", found)

    def test_reader_sees_only_authorized_current_unquarantined_chunks(self):
        self.assertEqual(self.search("knowledge:read")[0], "runbook")
        self.assertNotIn("incident", self.search("knowledge:read"))
        self.assertIn("incident", self.search("knowledge:read", "knowledge:restricted"))
        for hidden in ("tenant-b", "poisoned", "old"):
            self.assertNotIn(hidden, self.search("knowledge:read", "knowledge:restricted"))
        self.assertIn("tenant-b", self.search("knowledge:read", tenant="tenant-b"))
        self.assertEqual(self.search("ai:invoke"), [])

    def test_reader_cannot_write(self):
        with psycopg.connect(role_dsn("rpp_knowledge_reader")) as connection:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute("DELETE FROM knowledge_chunks")


if __name__ == "__main__":
    unittest.main()
