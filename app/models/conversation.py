from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.guest_session import GuestSession
    from app.models.message import Message
    from app.models.user import User


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A chat thread scoped to one document and one principal.

    Keyed by the same polymorphic-principal pattern as comments. A guest's chat
    history therefore survives a page refresh (their token points at the same
    guest session) but is invisible to the next visitor who opens the same
    share link, because that visitor gets a different guest session.
    """

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(user_id, guest_session_id) = 1",
            name="ck_conversation_exactly_one_owner",
        ),
        Index("ix_conversations_document_user", "document_id", "user_id"),
        Index("ix_conversations_document_guest", "document_id", "guest_session_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    guest_session_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("guest_sessions.id", ondelete="CASCADE"),
        nullable=True,
    )

    document: Mapped["Document"] = relationship(back_populates="conversations")
    user: Mapped["User | None"] = relationship()
    guest_session: Mapped["GuestSession | None"] = relationship()
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
    )
