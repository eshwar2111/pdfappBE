from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path

from app.api.deps import (
    CurrentPrincipal,
    DbSession,
    DocumentAccess,
    get_comment_service,
    require_document,
)
from app.controllers.comment_controller import CommentController
from app.domain.enums import Permission
from app.schemas.comment import (
    CommentResponse,
    CreateCommentRequest,
    UpdateCommentRequest,
)
from app.services.comment_service import CommentService

router = APIRouter(prefix="/documents", tags=["comments"])


def get_controller(
    session: DbSession,
    comment_service: Annotated[CommentService, Depends(get_comment_service)],
) -> CommentController:
    return CommentController(session, comment_service)


Controller = Annotated[CommentController, Depends(get_controller)]


@router.get("/{document_id}/comments", response_model=list[CommentResponse])
async def list_comments(
    principal: CurrentPrincipal,
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.VIEW))],
) -> list[CommentResponse]:
    """Threaded comments. Open to the owner and to any guest with VIEW."""
    return await controller.list_comments(
        document=access.document, principal=principal
    )


@router.post("/{document_id}/comments", response_model=list[CommentResponse])
async def create_comment(
    payload: CreateCommentRequest,
    principal: CurrentPrincipal,
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.COMMENT))],
) -> list[CommentResponse]:
    """Post a comment or a reply.

    Note the payload has no author field: authorship is taken from the caller's
    principal, so a guest posts as their guest session and nothing else.
    """
    return await controller.create(
        document=access.document, principal=principal, payload=payload
    )


@router.patch(
    "/{document_id}/comments/{comment_id}", response_model=list[CommentResponse]
)
async def update_comment(
    comment_id: Annotated[UUID, Path()],
    payload: UpdateCommentRequest,
    principal: CurrentPrincipal,
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.COMMENT))],
) -> list[CommentResponse]:
    """Authors only — enforced server-side against the calling principal."""
    return await controller.update(
        document=access.document,
        principal=principal,
        comment_id=comment_id,
        payload=payload,
    )


@router.delete(
    "/{document_id}/comments/{comment_id}", response_model=list[CommentResponse]
)
async def delete_comment(
    comment_id: Annotated[UUID, Path()],
    principal: CurrentPrincipal,
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.COMMENT))],
) -> list[CommentResponse]:
    """Soft delete, so replies keep their place in the thread. The author may
    delete their own; the document owner may delete any."""
    return await controller.delete(
        document=access.document, principal=principal, comment_id=comment_id
    )
