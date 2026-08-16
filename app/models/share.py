from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import ARRAY, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domain.enums import Permission
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.guest_session import GuestSession
    from app.models.user import User


class Share(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A capability-bearing link granting scoped access to one document."""

    __tablename__ = "shares"

    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: SHA-256 of the raw token. The raw value is returned to the creator once,
    #: at generation time, and is never recoverable from the database.
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    #: What the link grants. Stored explicitly rather than assumed, so a
    #: view-only link is a data change and not a code change.
    permissions: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), nullable=False
    )

    #: Optional address the link was emailed to. Purely informational — it is
    #: never used to authorize, because anyone holding the link is the bearer.
    invited_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    document: Mapped["Document"] = relationship(back_populates="shares")
    created_by: Mapped["User"] = relationship()
    guest_sessions: Mapped[list["GuestSession"]] = relationship(
        back_populates="share",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def permission_set(self) -> frozenset[Permission]:
        return frozenset(Permission(p) for p in self.permissions)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)

    @property
    def is_active(self) -> bool:
        return not self.is_revoked and not self.is_expired
