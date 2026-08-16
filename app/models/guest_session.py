from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.comment import Comment
    from app.models.share import Share


class GuestSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A server-issued identity for an invited, unauthenticated visitor.

    This exists so that a guest is a *real principal* with a database row and a
    stable id — not a display name posted alongside each comment. The client
    never supplies an author name at write time; it supplies a token that
    points here. Without this table, anyone could post a comment claiming to be
    the document owner.
    """

    __tablename__ = "guest_sessions"

    share_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("shares.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Collected once, when the visitor opens the link. Trimmed and length-capped
    #: by the schema layer; always rendered with a "Guest" badge in the UI so a
    #: visitor cannot visually impersonate a registered user.
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    share: Mapped["Share"] = relationship(back_populates="guest_sessions")
    comments: Mapped[list["Comment"]] = relationship(back_populates="author_guest")
