"""Share links and guest identity.

The guest-identity design in one line: a guest is a *server-issued principal*
with a database row, not a display name the client attaches to each write.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import settings
from app.core.security import create_guest_token, generate_share_token
from app.domain.enums import Permission
from app.email.base import EmailSender
from app.email.templates import share_invitation
from app.models.guest_session import GuestSession
from app.models.share import Share
from app.repositories.share_repository import GuestSessionRepository, ShareRepository
from app.schemas.share import (
    CreateShareRequest,
    GuestSessionResponse,
    ShareCreatedResponse,
    StartGuestSessionRequest,
)

logger = logging.getLogger(__name__)


class SharingService:
    def __init__(
        self,
        *,
        share_repository: ShareRepository,
        guest_session_repository: GuestSessionRepository,
        email_sender: EmailSender | None = None,
    ) -> None:
        self._shares = share_repository
        self._guests = guest_session_repository
        self._email = email_sender

    async def notify_invitee(
        self,
        *,
        share: ShareCreatedResponse,
        document_name: str,
        owner_name: str,
    ) -> bool:
        """Email the share link, if an address was supplied.

        Called after the transaction commits: the link is valid regardless of
        whether delivery succeeds, and a bounced email must not roll back a
        created share. The owner always gets the URL in the response, so email
        is a convenience rather than the delivery mechanism.
        """
        if self._email is None or not share.invited_email:
            return False

        return await self._email.send(
            share_invitation(
                to=share.invited_email,
                document_name=document_name,
                owner_name=owner_name,
                share_url=share.url,
                can_comment=Permission.COMMENT in share.permissions,
            )
        )

    async def create_share(
        self,
        *,
        document_id: UUID,
        created_by_user_id: UUID,
        payload: CreateShareRequest,
        frontend_base_url: str,
    ) -> ShareCreatedResponse:
        raw_token, token_hash = generate_share_token()

        expires_at = (
            datetime.now(UTC) + timedelta(hours=payload.expires_in_hours)
            if payload.expires_in_hours
            else None
        )

        share = await self._shares.add(
            Share(
                document_id=document_id,
                created_by_user_id=created_by_user_id,
                token_hash=token_hash,
                permissions=[p.value for p in payload.permissions],
                invited_email=payload.invited_email,
                expires_at=expires_at,
            )
        )

        # The only moment the raw token exists outside the recipient's URL bar.
        # It is not recoverable afterwards — only its digest was stored.
        return ShareCreatedResponse(
            id=share.id,
            document_id=share.document_id,
            permissions=payload.permissions,
            invited_email=share.invited_email,
            expires_at=share.expires_at,
            revoked_at=None,
            created_at=share.created_at,
            url=f"{frontend_base_url.rstrip('/')}/s/{raw_token}",
        )

    async def list_shares(self, document_id: UUID) -> list[Share]:
        return await self._shares.list_for_document(document_id)

    async def revoke(self, share: Share) -> None:
        await self._shares.revoke(share)

    async def start_guest_session(
        self, *, share: Share, payload: StartGuestSessionRequest
    ) -> GuestSessionResponse:
        """Mint a guest identity and a token scoped to this share's document.

        The display name is persisted here, once, and every later write reads
        it from this row. The client is never trusted to restate who it is.
        """
        guest = await self._guests.add(
            GuestSession(share_id=share.id, display_name=payload.display_name)
        )

        permissions = share.permission_set - {Permission.MANAGE}
        token = create_guest_token(
            guest_session_id=guest.id,
            share_id=share.id,
            document_id=share.document_id,
            permissions=permissions,
        )

        return GuestSessionResponse(
            access_token=token,
            expires_in=settings.guest_token_ttl_minutes * 60,
            guest_session_id=guest.id,
            display_name=guest.display_name,
            document_id=share.document_id,
            permissions=sorted(permissions),
        )
