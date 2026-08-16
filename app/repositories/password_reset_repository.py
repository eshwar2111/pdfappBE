from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.models.password_reset_token import PasswordResetToken
from app.repositories.base import BaseRepository


class PasswordResetRepository(BaseRepository[PasswordResetToken]):
    model = PasswordResetToken

    async def get_by_token_hash(self, token_hash: str) -> PasswordResetToken | None:
        result = await self.session.execute(
            select(PasswordResetToken)
            .where(PasswordResetToken.token_hash == token_hash)
            .options(selectinload(PasswordResetToken.user))
        )
        return result.scalar_one_or_none()

    async def invalidate_outstanding(self, user_id: UUID) -> None:
        """Burn any unused tokens for this user.

        Requesting a new link should void the previous one — otherwise every
        historical email remains a working key until it expires on its own.
        """
        await self.session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=datetime.now(UTC))
        )
        await self.session.flush()

    async def mark_used(self, token: PasswordResetToken) -> None:
        token.used_at = datetime.now(UTC)
        await self.session.flush()
