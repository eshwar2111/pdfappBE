from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import (
    CurrentUser,
    DbSession,
    DocumentAccess,
    get_authorization_service,
    get_document_repository,
    get_frontend_base_url,
    get_sharing_service,
    get_user_repository,
    require_document,
)
from app.controllers.share_controller import ShareController
from app.domain.enums import Permission
from app.repositories.document_repository import DocumentRepository
from app.repositories.user_repository import UserRepository
from app.schemas.share import (
    CreateShareRequest,
    GuestSessionResponse,
    ShareCreatedResponse,
    SharePreviewResponse,
    ShareResponse,
    StartGuestSessionRequest,
)
from app.services.authorization_service import AuthorizationService
from app.services.sharing_service import SharingService


def get_controller(
    session: DbSession,
    sharing_service: Annotated[SharingService, Depends(get_sharing_service)],
    authorization_service: Annotated[
        AuthorizationService, Depends(get_authorization_service)
    ],
    document_repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    user_repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> ShareController:
    return ShareController(
        session,
        sharing_service,
        authorization_service,
        document_repository,
        user_repository,
    )


Controller = Annotated[ShareController, Depends(get_controller)]

# --- Owner-facing: managing a document's share links -------------------------
document_shares_router = APIRouter(prefix="/documents", tags=["sharing"])


@document_shares_router.post(
    "/{document_id}/shares",
    response_model=ShareCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_share(
    payload: CreateShareRequest,
    principal: CurrentUser,
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.MANAGE))],
    frontend_base_url: Annotated[str, Depends(get_frontend_base_url)],
) -> ShareCreatedResponse:
    """Generate a share link.

    The response carries the only copy of the raw token that will ever exist —
    the database stores just its SHA-256 digest.
    """
    return await controller.create(
        document_id=access.document.id,
        created_by_user_id=principal.id,
        payload=payload,
        frontend_base_url=frontend_base_url,
    )


@document_shares_router.get(
    "/{document_id}/shares", response_model=list[ShareResponse]
)
async def list_shares(
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.MANAGE))],
) -> list[ShareResponse]:
    return await controller.list_shares(access.document.id)


@document_shares_router.delete(
    "/{document_id}/shares/{share_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def revoke_share(
    share_id: Annotated[UUID, Path()],
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.MANAGE))],
) -> None:
    """Revocation is immediate: guest tokens are re-validated against the share
    row on every request, so outstanding sessions stop working at once."""
    await controller.revoke(document_id=access.document.id, share_id=share_id)


# --- Guest-facing: redeeming a link (no authentication) ----------------------
public_shares_router = APIRouter(prefix="/shares", tags=["sharing"])


@public_shares_router.get("/{token}", response_model=SharePreviewResponse)
async def preview_share(
    token: Annotated[str, Path(min_length=16, max_length=128)],
    controller: Controller,
) -> SharePreviewResponse:
    """Unauthenticated. Reveals only the filename, owner and granted
    permissions — enough for the visitor to recognise the invitation."""
    return await controller.preview(token)


@public_shares_router.post(
    "/{token}/session",
    response_model=GuestSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_guest_session(
    token: Annotated[str, Path(min_length=16, max_length=128)],
    payload: StartGuestSessionRequest,
    controller: Controller,
) -> GuestSessionResponse:
    """Exchange a share link for a guest identity.

    Creates a server-side guest session row and returns a token scoped to that
    one document. Every later write reads the display name from the row, so a
    guest cannot post under someone else's name.
    """
    return await controller.start_guest_session(raw_token=token, payload=payload)
