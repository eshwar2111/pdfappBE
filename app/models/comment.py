from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.guest_session import GuestSession
    from app.models.user import User


class Comment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A comment authored by either a registered user or an invited guest.

    Authorship is polymorphic across two nullable foreign keys, with a database
    ``CHECK`` guaranteeing exactly one is set. That constraint is the reason
    there is no representable state in which a comment has an unattributed or
    doubly-attributed author — it is enforced by Postgres, not by convention.
    """

    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(author_user_id, author_guest_id) = 1",
            name="ck_comment_exactly_one_author",
        ),
        Index("ix_comments_document_created", "document_id", "created_at"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Self-referential parent for threaded replies. One level is what the UI
    #: renders; the column itself imposes no depth limit.
    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    author_guest_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("guest_sessions.id", ondelete="CASCADE"),
        nullable=True,
    )

    #: Markdown-subset source (bold, italic, lists). Sanitised on render, and
    #: stored as written so it can be re-rendered if the renderer changes.
    body: Mapped[str] = mapped_column(Text, nullable=False)

    #: Soft delete — threads keep their shape when a parent is removed.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    document: Mapped["Document"] = relationship(back_populates="comments")
    author_user: Mapped["User | None"] = relationship(back_populates="comments")
    author_guest: Mapped["GuestSession | None"] = relationship(back_populates="comments")
    parent: Mapped["Comment | None"] = relationship(
        remote_side="Comment.id", back_populates="replies"
    )
    replies: Mapped[list["Comment"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
