-- Knowledge index schema, applied idempotently by the operations-knowledge ingestion job as the
-- table-owning writer role. The platform creates the database, the vector extension and both roles.
CREATE TABLE IF NOT EXISTS knowledge_documents (
    document_id text NOT NULL,
    document_version text NOT NULL,
    title text NOT NULL,
    doc_type text NOT NULL,
    tenant_id text NOT NULL,
    classification text NOT NULL CHECK (classification IN ('public', 'internal', 'restricted')),
    required_permission text NOT NULL,
    source_uri text NOT NULL,
    object_key text NOT NULL,
    content_hash text NOT NULL,
    quarantined boolean NOT NULL DEFAULT false,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    PRIMARY KEY (document_id, document_version)
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL,
    document_version text NOT NULL,
    chunk_index integer NOT NULL,
    heading text NOT NULL,
    content text NOT NULL,
    content_tsvector tsvector GENERATED ALWAYS AS (to_tsvector('english', heading || ' ' || content)) STORED,
    embedding vector(768) NOT NULL,
    token_count integer NOT NULL,
    tenant_id text NOT NULL,
    classification text NOT NULL,
    required_permission text NOT NULL,
    source_uri text NOT NULL,
    content_hash text NOT NULL,
    embedding_model text NOT NULL,
    embedding_version text NOT NULL,
    chunking_version text NOT NULL,
    quarantined boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    FOREIGN KEY (document_id, document_version) REFERENCES knowledge_documents (document_id, document_version)
);

CREATE INDEX IF NOT EXISTS knowledge_chunks_text ON knowledge_chunks USING gin (content_tsvector);
CREATE INDEX IF NOT EXISTS knowledge_chunks_current
    ON knowledge_chunks (required_permission, tenant_id) WHERE valid_to IS NULL AND NOT quarantined;

GRANT SELECT ON knowledge_documents, knowledge_chunks TO rpp_knowledge_reader;
