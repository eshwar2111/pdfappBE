from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domain.enums import DocumentStatus, ProcessingFailureReason
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.comment import Comment
    from app.models.conversation import Conversation
    from app.models.document_chunk import DocumentChunk
    from app.models.share import Share
    from app.models.user import User


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user-uploaded PDF and the state of its AI pipeline.

    The binary never lives here — only the blob key. Postgres stores metadata,
    blob storage stores bytes.
    """

    __tablename__ = "documents"
    __table_args__ = (
        # The dashboard's hot path: a user's own documents, newest first.
        Index("ix_documents_owner_created", "owner_id", "created_at"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: Opaque key within the private container. Never exposed to the client;
    #: the client only ever receives a short-lived signed URL.
    blob_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", native_enum=False, length=20),
        nullable=False,
        default=DocumentStatus.UPLOADED,
        index=True,
    )
    failure_reason: Mapped[ProcessingFailureReason | None] = mapped_column(
        Enum(
            ProcessingFailureReason,
            name="processing_failure_reason",
            native_enum=False,
            length=32,
        ),
        nullable=True,
    )

    #: 3–5 sentence AI summary, shown on the dashboard card and above the viewer.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    owner: Mapped["User"] = relationship(back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    shares: Mapped[list["Share"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    comments: Mapped[list["Comment"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def is_ready(self) -> bool:
        return self.status is DocumentStatus.READY
