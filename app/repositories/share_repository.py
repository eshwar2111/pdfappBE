from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.guest_session import GuestSession
from app.models.share import Share
from app.repositories.base import BaseRepository


class ShareRepository(BaseRepository[Share]):
    model = Share

    async def get_by_token_hash(self, token_hash: str) -> Share | None:
        """Lookup is by digest, never by raw token — the raw value is not stored."""
        result = await self.session.execute(
            select(Share)
            .where(Share.token_hash == token_hash)
            .options(selectinload(Share.document))
        )
        return result.scalar_one_or_none()

    async def list_for_document(self, document_id: UUID) -> list[Share]:
        result = await self.session.execute(
            select(Share)
            .where(Share.document_id == document_id)
            .order_by(Share.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke(self, share: Share) -> None:
        share.revoked_at = datetime.now(UTC)
        await self.session.flush()


class GuestSessionRepository(BaseRepository[GuestSession]):
    model = GuestSession

    async def get_with_share(self, guest_session_id: UUID) -> GuestSession | None:
        """Loads the parent share alongside the session.

        Every guest request re-reads the share this way so that revoking a link
        takes effect immediately, even though the guest's JWT has not expired.
        """
        result = await self.session.execute(
            select(GuestSession)
            .where(GuestSession.id == guest_session_id)
            .options(selectinload(GuestSession.share))
        )
        return result.scalar_one_or_none()

    async def touch(self, guest_session: GuestSession) -> None:
        guest_session.last_seen_at = datetime.now(UTC)
        await self.session.flush()
