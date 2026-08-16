from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.comment import Comment
from app.repositories.base import BaseRepository


class CommentRepository(BaseRepository[Comment]):
    model = Comment

    async def list_for_document(self, document_id: UUID) -> list[Comment]:
        """Flat, chronological fetch with both possible authors eager-loaded.

        The service assembles the tree in memory. Two eager loads beat the N+1
        that lazy-loading polymorphic authors would otherwise produce on a
        busy comment sidebar.
        """
        result = await self.session.execute(
            select(Comment)
            .where(Comment.document_id == document_id)
            .options(
                selectinload(Comment.author_user),
                selectinload(Comment.author_guest),
            )
            .order_by(Comment.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_in_document(
        self, comment_id: UUID, document_id: UUID
    ) -> Comment | None:
        """Scoped fetch: a comment id from another document can never resolve,
        so a caller cannot reach across documents by guessing ids."""
        result = await self.session.execute(
            select(Comment)
            .where(Comment.id == comment_id, Comment.document_id == document_id)
            .options(
                selectinload(Comment.author_user),
                selectinload(Comment.author_guest),
            )
        )
        return result.scalar_one_or_none()
