from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.document import Document


class DocumentChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A retrievable slice of a document, with its embedding.

    Chunks and their vectors live in the same Postgres database as everything
    else, via pgvector. That buys two things a separate vector store cannot:
    the chunk write and the document status update commit in one transaction,
    and similarity search is filtered by ``document_id`` *inside the query* —
    so the authorization boundary is enforced by SQL rather than by trusting a
    metadata filter in a second system.
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_document_index"),
        Index("ix_chunks_document", "document_id"),
        # HNSW over cosine distance. Built after ingest; for the corpus sizes
        # this app sees, Postgres will often prefer a scan of the (already
        # document-filtered) rows, which is correct and fast.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    #: 1-based page range this chunk was drawn from — used to cite sources
    #: back to the user in chat answers.
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)

    token_count: Mapped[int] = mapped_column(Integer, nullable=False)

    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.gemini_embedding_dimensions),
        nullable=False,
    )

    #: Lexical half of hybrid retrieval. A generated column, so Postgres keeps
    #: it in sync with `content` automatically — SQLAlchemy omits Computed
    #: columns from INSERT, which is what makes that safe.
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=True,
    )

    document: Mapped["Document"] = relationship(back_populates="chunks")
