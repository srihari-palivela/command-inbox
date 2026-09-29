"""Knowledge that drafts can cite: uploaded documents, parsed into chunks, retrieved by hybrid search.

- `knowledge_docs` gains the document's lifecycle: version and the version it replaces, checksum, file
  metadata, the original sealed with the tenant key, parsing and antivirus state, effective and expiry
  dates, and who uploaded and who approved it. Only approved, in-effect documents are ever retrieved.
- `knowledge_chunks`: structure-aware pieces of a document with their section path and page, a full-text
  vector (generated) and an embedding (pgvector, HNSW index, cosine distance).

Revision ID: 0009_knowledge_retrieval
Revises: 0008_mail_connectors
"""

from __future__ import annotations

from alembic import op

revision = "0009_knowledge_retrieval"
down_revision = "0008_mail_connectors"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1024


def _run(sql: str) -> None:
    for statement in sql.split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(
        f"""
        CREATE EXTENSION IF NOT EXISTS vector;
        ALTER TABLE knowledge_docs
          ADD COLUMN version int NOT NULL DEFAULT 1,
          ADD COLUMN replaces_id uuid,
          ADD COLUMN checksum text,
          ADD COLUMN filename text NOT NULL DEFAULT '',
          ADD COLUMN content_type text NOT NULL DEFAULT '',
          ADD COLUMN size int NOT NULL DEFAULT 0,
          ADD COLUMN blob_sealed text,
          ADD COLUMN parse_status text NOT NULL DEFAULT 'none',
          ADD COLUMN parse_error text NOT NULL DEFAULT '',
          ADD COLUMN av_status text NOT NULL DEFAULT 'not_scanned',
          ADD COLUMN effective_from timestamptz,
          ADD COLUMN expires_at timestamptz,
          ADD COLUMN uploaded_by uuid,
          ADD COLUMN approved_by uuid,
          ADD COLUMN approved_at timestamptz,
          ADD COLUMN chunk_count int NOT NULL DEFAULT 0,
          ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
          ADD CONSTRAINT knowledge_docs_parse_ck CHECK (parse_status in
            ('none', 'queued', 'scanning', 'parsing', 'ready', 'failed', 'infected')),
          ADD CONSTRAINT knowledge_docs_av_ck CHECK (av_status in ('not_scanned', 'clean', 'infected', 'error')),
          ADD CONSTRAINT knowledge_docs_org_id_id_uq UNIQUE (org_id, id);
        CREATE INDEX knowledge_docs_checksum_idx ON knowledge_docs (org_id, checksum);

        CREATE TABLE knowledge_chunks (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL,
          doc_id uuid NOT NULL,
          ordinal int NOT NULL,
          section_path text NOT NULL DEFAULT '',
          page int,
          text text NOT NULL,
          tokens int NOT NULL DEFAULT 0,
          tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', section_path || ' ' || text)) STORED,
          embedding vector({EMBEDDING_DIM}),
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT knowledge_chunks_doc_fk FOREIGN KEY (org_id, doc_id)
            REFERENCES knowledge_docs (org_id, id) ON DELETE CASCADE
        );
        CREATE INDEX knowledge_chunks_doc_idx ON knowledge_chunks (org_id, doc_id, ordinal);
        CREATE INDEX knowledge_chunks_tsv_idx ON knowledge_chunks USING gin (tsv);
        CREATE INDEX knowledge_chunks_embedding_idx ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
        SELECT ci_enable_tenant_rls('knowledge_chunks')
        """
    )


def downgrade() -> None:
    _run(
        """
        DROP TABLE knowledge_chunks;
        DROP INDEX IF EXISTS knowledge_docs_checksum_idx;
        ALTER TABLE knowledge_docs
          DROP CONSTRAINT knowledge_docs_org_id_id_uq,
          DROP CONSTRAINT knowledge_docs_av_ck,
          DROP CONSTRAINT knowledge_docs_parse_ck,
          DROP COLUMN created_at,
          DROP COLUMN chunk_count,
          DROP COLUMN approved_at,
          DROP COLUMN approved_by,
          DROP COLUMN uploaded_by,
          DROP COLUMN expires_at,
          DROP COLUMN effective_from,
          DROP COLUMN av_status,
          DROP COLUMN parse_error,
          DROP COLUMN parse_status,
          DROP COLUMN blob_sealed,
          DROP COLUMN size,
          DROP COLUMN content_type,
          DROP COLUMN filename,
          DROP COLUMN checksum,
          DROP COLUMN replaces_id,
          DROP COLUMN version
        """
    )
