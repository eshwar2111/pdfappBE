from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domain.enums import MessageRole
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.conversation import Conversation


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )

    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role", native_enum=False, length=16),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    #: For assistant turns: which chunks grounded the answer, as
    #: ``[{"chunk_id": ..., "page_start": ..., "page_end": ...}]``. Persisted so
    #: citations survive a reload and so answers remain auditable after the fact.
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
