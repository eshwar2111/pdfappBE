from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.domain.enums import DEFAULT_SHARE_PERMISSIONS, Permission
from app.schemas.common import APISchema


class CreateShareRequest(APISchema):
    permissions: list[Permission] = Field(
        default_factory=lambda: sorted(DEFAULT_SHARE_PERMISSIONS),
        description="Capabilities the link grants. MANAGE is never shareable.",
    )
    expires_in_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    invited_email: EmailStr | None = None

    @field_validator("permissions")
    @classmethod
    def _reject_manage(cls, value: list[Permission]) -> list[Permission]:
        if Permission.MANAGE in value:
            raise ValueError("A share link cannot grant MANAGE.")
        if not value:
            raise ValueError("A share link must grant at least one permission.")
        return value


class ShareResponse(APISchema):
    id: UUID
    document_id: UUID
    permissions: list[Permission]
    invited_email: EmailStr | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ShareCreatedResponse(ShareResponse):
    """Returned exactly once, at creation.

    ``url`` embeds the raw token, which is not stored in recoverable form —
    only its SHA-256 digest is. If the owner loses this URL they must issue a
    new link.
    """

    url: str


class StartGuestSessionRequest(APISchema):
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("display_name")
    @classmethod
    def _clean(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Please enter a display name.")
        return cleaned


class GuestSessionResponse(APISchema):
    """The guest's scoped credential plus the document it unlocks."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    guest_session_id: UUID
    display_name: str
    document_id: UUID
    permissions: list[Permission]


class SharePreviewResponse(APISchema):
    """Shown before the visitor identifies themselves, so they know what they
    are about to open. Deliberately minimal — filename only, no content."""

    document_id: UUID
    filename: str
    permissions: list[Permission]
    owner_name: str
