"""Hybrid search: full-text vectors on chunks, trigram index on filenames

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-16

Dense (embedding) retrieval and lexical (BM25-family) retrieval fail in
opposite directions. Embeddings capture meaning but blur rare tokens — a
company name like "MariApps" lands near every other proper noun in the corpus.
Postgres full-text ranking is the reverse: exact on rare terms, useless for
paraphrase. Running both and fusing the rankings covers each other's blind spot.

`ts_rank_cd` over a `tsvector` is not literally Okapi BM25, but it is the same
family — term frequency weighted by inverse document frequency and normalised
by length — and it comes free with the database we already run.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Trigram matching for filenames, so "mariapps" still finds
    # "MariApps_Agreement_v3.pdf" despite the casing and surrounding tokens.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # A generated column rather than a trigger: Postgres keeps it in sync on
    # every write, so there is no path where a chunk exists without its
    # search vector.
    op.execute(
        """
        ALTER TABLE document_chunks
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
        """
    )
    op.execute(
        "CREATE INDEX ix_chunks_content_tsv ON document_chunks USING GIN (content_tsv)"
    )
    op.execute(
        "CREATE INDEX ix_documents_filename_trgm "
        "ON documents USING GIN (filename gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_documents_filename_trgm")
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_tsv")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS content_tsv")
    # pg_trgm is left installed; other schemas may depend on it.
