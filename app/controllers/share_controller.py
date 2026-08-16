from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ShareNotFoundError
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


class ShareController:
    def __init__(
        self,
        session: AsyncSession,
        sharing_service: SharingService,
        authorization_service: AuthorizationService,
        document_repository: DocumentRepository,
        user_repository: UserRepository,
    ) -> None:
        self._session = session
        self._sharing = sharing_service
        self._authorization = authorization_service
        self._documents = document_repository
        self._users = user_repository

    async def create(
        self,
        *,
        document_id: UUID,
        created_by_user_id: UUID,
        payload: CreateShareRequest,
        frontend_base_url: str,
    ) -> ShareCreatedResponse:
        response = await self._sharing.create_share(
            document_id=document_id,
            created_by_user_id=created_by_user_id,
            payload=payload,
            frontend_base_url=frontend_base_url,
        )
        await self._session.commit()

        # Notify after the commit, never inside it. The share exists whether or
        # not the email lands, and an SMTP timeout must not roll back a link
        # the owner already has in the response.
        if response.invited_email:
            document = await self._documents.get(document_id)
            owner = await self._users.get(created_by_user_id)
            await self._sharing.notify_invitee(
                share=response,
                document_name=document.filename if document else "a document",
                owner_name=owner.name if owner else "Someone",
            )

        return response

    async def list_shares(self, document_id: UUID) -> list[ShareResponse]:
        shares = await self._sharing.list_shares(document_id)
        return [
            ShareResponse(
                id=share.id,
                document_id=share.document_id,
                permissions=sorted(share.permission_set),
                invited_email=share.invited_email,
                expires_at=share.expires_at,
                revoked_at=share.revoked_at,
                created_at=share.created_at,
            )
            for share in shares
        ]

    async def revoke(self, *, document_id: UUID, share_id: UUID) -> None:
        shares = await self._sharing.list_shares(document_id)
        target = next((s for s in shares if s.id == share_id), None)
        if target is None:
            raise ShareNotFoundError()
        await self._sharing.revoke(target)
        await self._session.commit()

    async def preview(self, raw_token: str) -> SharePreviewResponse:
        """What the visitor sees before identifying themselves.

        Filename and owner only — enough to recognise the invitation, nothing
        of the document's contents, since no principal exists yet.
        """
        share = await self._authorization.resolve_active_share(raw_token)
        document = await self._documents.get(share.document_id)
        if document is None:
            raise ShareNotFoundError()
        owner = await self._users.get(document.owner_id)

        return SharePreviewResponse(
            document_id=document.id,
            filename=document.filename,
            permissions=sorted(share.permission_set - {Permission.MANAGE}),
            owner_name=owner.name if owner else "Unknown",
        )

    async def start_guest_session(
        self, *, raw_token: str, payload: StartGuestSessionRequest
    ) -> GuestSessionResponse:
        share = await self._authorization.resolve_active_share(raw_token)
        response = await self._sharing.start_guest_session(
            share=share, payload=payload
        )
        await self._session.commit()
        return response
