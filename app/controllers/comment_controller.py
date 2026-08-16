from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.principal import Principal
from app.models.document import Document
from app.schemas.comment import (
    CommentResponse,
    CreateCommentRequest,
    UpdateCommentRequest,
)
from app.services.comment_service import CommentService


class CommentController:
    def __init__(self, session: AsyncSession, comment_service: CommentService) -> None:
        self._session = session
        self._comments = comment_service

    async def list_comments(
        self, *, document: Document, principal: Principal
    ) -> list[CommentResponse]:
        return await self._comments.list_thread(document=document, principal=principal)

    async def create(
        self,
        *,
        document: Document,
        principal: Principal,
        payload: CreateCommentRequest,
    ) -> list[CommentResponse]:
        """Returns the whole thread rather than the single new comment.

        The sidebar is a tree whose shape changes when a reply lands, so one
        round trip returning the new state beats a client-side patch that has
        to reconstruct it.
        """
        await self._comments.create(
            document=document, principal=principal, payload=payload
        )
        await self._session.commit()
        return await self._comments.list_thread(document=document, principal=principal)

    async def update(
        self,
        *,
        document: Document,
        principal: Principal,
        comment_id: UUID,
        payload: UpdateCommentRequest,
    ) -> list[CommentResponse]:
        await self._comments.update(
            document=document,
            principal=principal,
            comment_id=comment_id,
            payload=payload,
        )
        await self._session.commit()
        return await self._comments.list_thread(document=document, principal=principal)

    async def delete(
        self, *, document: Document, principal: Principal, comment_id: UUID
    ) -> list[CommentResponse]:
        await self._comments.delete(
            document=document, principal=principal, comment_id=comment_id
        )
        await self._session.commit()
        return await self._comments.list_thread(document=document, principal=principal)
