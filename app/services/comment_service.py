"""Comments, including polymorphic authorship across users and guests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.exceptions import AuthorizationError, CommentNotFoundError, ValidationError
from app.domain.enums import PrincipalKind
from app.domain.principal import Principal
from app.models.comment import Comment
from app.models.document import Document
from app.repositories.comment_repository import CommentRepository
from app.schemas.comment import (
    AuthorRef,
    CommentResponse,
    CreateCommentRequest,
    UpdateCommentRequest,
)

_DELETED_PLACEHOLDER = "[deleted]"


class CommentService:
    def __init__(self, comment_repository: CommentRepository) -> None:
        self._comments = comment_repository

    async def create(
        self,
        *,
        document: Document,
        principal: Principal,
        payload: CreateCommentRequest,
    ) -> Comment:
        """Authorship is derived from the caller's token, never from the body.

        There is no field on ``CreateCommentRequest`` for an author name — that
        is the whole point. A guest posts as the guest session their share link
        created, so impersonating the owner is not expressible.
        """
        if payload.parent_comment_id is not None:
            parent = await self._comments.get_in_document(
                payload.parent_comment_id, document.id
            )
            if parent is None:
                raise ValidationError("The comment you replied to no longer exists.")
            if parent.parent_comment_id is not None:
                # One level of nesting. Replies to replies attach to the thread
                # root so the sidebar cannot degenerate into deep indentation.
                payload = payload.model_copy(
                    update={"parent_comment_id": parent.parent_comment_id}
                )

        return await self._comments.add(
            Comment(
                document_id=document.id,
                parent_comment_id=payload.parent_comment_id,
                author_user_id=principal.user_id,
                author_guest_id=principal.guest_session_id,
                body=payload.body,
            )
        )

    async def update(
        self, *, document: Document, principal: Principal, comment_id: UUID,
        payload: UpdateCommentRequest,
    ) -> Comment:
        comment = await self._require_own_comment(document, principal, comment_id)
        comment.body = payload.body
        return comment

    async def delete(
        self, *, document: Document, principal: Principal, comment_id: UUID
    ) -> None:
        """Soft delete.

        A hard delete would take the replies with it via cascade; keeping the
        row preserves the shape of the thread. The document owner may remove
        any comment on their document; everyone else, only their own.
        """
        comment = await self._comments.get_in_document(comment_id, document.id)
        if comment is None:
            raise CommentNotFoundError()

        is_owner = principal.is_user and document.owner_id == principal.id
        if not is_owner and not self._is_author(comment, principal):
            raise AuthorizationError("You can only delete your own comments.")

        comment.deleted_at = datetime.now(UTC)

    async def list_thread(
        self, *, document: Document, principal: Principal
    ) -> list[CommentResponse]:
        """Assemble the flat, chronological rows into one level of threading."""
        comments = await self._comments.list_for_document(document.id)

        roots: list[CommentResponse] = []
        by_id: dict[UUID, CommentResponse] = {}

        for comment in comments:
            if comment.parent_comment_id is None:
                dto = self._to_response(comment, document, principal)
                by_id[comment.id] = dto
                roots.append(dto)

        for comment in comments:
            if comment.parent_comment_id is None:
                continue
            parent = by_id.get(comment.parent_comment_id)
            if parent is not None:
                parent.replies.append(self._to_response(comment, document, principal))

        # A deleted root with no surviving replies carries no information.
        return [
            root for root in roots if not (root.is_deleted and not root.replies)
        ]

    # --- internals ---------------------------------------------------------
    async def _require_own_comment(
        self, document: Document, principal: Principal, comment_id: UUID
    ) -> Comment:
        comment = await self._comments.get_in_document(comment_id, document.id)
        if comment is None:
            raise CommentNotFoundError()
        if comment.is_deleted:
            raise CommentNotFoundError()
        if not self._is_author(comment, principal):
            raise AuthorizationError("You can only edit your own comments.")
        return comment

    @staticmethod
    def _is_author(comment: Comment, principal: Principal) -> bool:
        if principal.is_user:
            return comment.author_user_id == principal.id
        return comment.author_guest_id == principal.id

    def _to_response(
        self, comment: Comment, document: Document, principal: Principal
    ) -> CommentResponse:
        author: AuthorRef | None = None
        if not comment.is_deleted:
            if comment.author_user is not None:
                author = AuthorRef(
                    kind=PrincipalKind.USER,
                    id=comment.author_user.id,
                    display_name=comment.author_user.name,
                    is_document_owner=comment.author_user.id == document.owner_id,
                )
            elif comment.author_guest is not None:
                author = AuthorRef(
                    kind=PrincipalKind.GUEST,
                    id=comment.author_guest.id,
                    display_name=comment.author_guest.display_name,
                    is_document_owner=False,
                )

        return CommentResponse(
            id=comment.id,
            document_id=comment.document_id,
            parent_comment_id=comment.parent_comment_id,
            author=author,
            body=_DELETED_PLACEHOLDER if comment.is_deleted else comment.body,
            is_deleted=comment.is_deleted,
            can_edit=not comment.is_deleted and self._is_author(comment, principal),
            created_at=comment.created_at,
            updated_at=comment.updated_at,
            replies=[],
        )
