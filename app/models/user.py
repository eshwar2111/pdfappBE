from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.comment import Comment
    from app.models.document import Document


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    #: Stored lowercase and uniquely indexed. The application normalises on the
    #: way in so "A@x.com" and "a@x.com" can never become two accounts.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)

    #: Argon2id PHC string. There is no column that can hold a plaintext password.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )

    documents: Mapped[list["Document"]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    comments: Mapped[list["Comment"]] = relationship(back_populates="author_user")
