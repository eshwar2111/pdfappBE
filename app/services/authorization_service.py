"""Principal resolution and document authorization.

Two responsibilities, deliberately together because they are the two halves of
one question:

    resolve_principal  - who is calling?      (authentication)
    authorize_document - may they do this?    (authorization)

Every protected route runs both. React never decides access; it only decides
what to render, using permissions the server already computed.
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import (
    AuthorizationError,
    DocumentNotFoundError,
    InvalidTokenError,
    ShareNotFoundError,
)
from app.core.security import decode_token, hash_share_token
from app.domain.enums import OWNER_PERMISSIONS, Permission, PrincipalKind, TokenType
from app.domain.principal import Principal
from app.models.document import Document
from app.repositories.document_repository import DocumentRepository
from app.repositories.share_repository import GuestSessionRepository, ShareRepository
from app.repositories.user_repository import UserRepository


class AuthorizationService:
    def __init__(
        self,
        *,
        user_repository: UserRepository,
        document_repository: DocumentRepository,
        share_repository: ShareRepository,
        guest_session_repository: GuestSessionRepository,
    ) -> None:
        self._users = user_repository
        self._documents = document_repository
        self._shares = share_repository
        self._guests = guest_session_repository

    # --- authentication ----------------------------------------------------
    async def resolve_principal(self, token: str) -> Principal:
        """Turn a bearer token into a ``Principal``.

        The ``typ`` claim decides which branch runs — the two token kinds are
        never interchangeable, so a guest token cannot be replayed against a
        user-only endpoint and vice versa.
        """
        claims = decode_token(token)
        token_type = claims.get("typ")

        if token_type == TokenType.ACCESS.value:
            return await self._principal_from_access_token(claims)
        if token_type == TokenType.GUEST.value:
            return await self._principal_from_guest_token(claims)
        raise InvalidTokenError()

    async def _principal_from_access_token(self, claims: dict) -> Principal:
        user = await self._users.get(UUID(claims["sub"]))
        if user is None or not user.is_active:
            raise InvalidTokenError("This account is no longer active.")
        return Principal(
            kind=PrincipalKind.USER,
            id=user.id,
            display_name=user.name,
            permissions=OWNER_PERMISSIONS,
            document_scope=None,
        )

    async def _principal_from_guest_token(self, claims: dict) -> Principal:
        guest = await self._guests.get_with_share(UUID(claims["sub"]))
        if guest is None:
            raise InvalidTokenError()

        share = guest.share
        # Re-read the share on every request rather than trusting the token's
        # embedded claims. Revoking a link therefore takes effect immediately,
        # instead of when the guest's JWT eventually expires.
        if share is None or not share.is_active:
            raise ShareNotFoundError()

        return Principal(
            kind=PrincipalKind.GUEST,
            id=guest.id,
            display_name=guest.display_name,
            permissions=share.permission_set,
            document_scope=share.document_id,
            share_id=share.id,
        )

    # --- authorization -----------------------------------------------------
    async def authorize_document(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        required: Permission,
    ) -> tuple[Document, frozenset[Permission]]:
        """Return the document and the caller's effective permissions on it.

        Ordering matters. The guest's token scope is checked *before* the
        document is loaded, and a caller who fails authorization gets 404 rather
        than 403 for documents they have no relationship with — a 403 would
        confirm the id exists, which is an enumeration oracle.
        """
        if not principal.may_reach(document_id):
            raise DocumentNotFoundError()

        document = await self._documents.get(document_id)
        if document is None:
            raise DocumentNotFoundError()

        effective = await self._effective_permissions(principal, document)
        if not effective:
            raise DocumentNotFoundError()
        if required not in effective:
            raise AuthorizationError(
                f"This link does not allow you to {required.value.lower()} on this document."
            )
        return document, effective

    async def _effective_permissions(
        self, principal: Principal, document: Document
    ) -> frozenset[Permission]:
        if principal.is_user:
            # Ownership is the only path to a document for a registered user.
            # A user opening someone else's share link acts as a guest for it.
            if document.owner_id == principal.id:
                return OWNER_PERMISSIONS
            return frozenset()

        # Guest: the token's scope already pinned the document, and the share
        # was re-validated during principal resolution.
        if principal.document_scope != document.id:
            return frozenset()
        return principal.permissions - {Permission.MANAGE}

    async def resolve_active_share(self, raw_token: str):
        """Look a share up by its raw token, rejecting revoked/expired links."""
        share = await self._shares.get_by_token_hash(hash_share_token(raw_token))
        if share is None or not share.is_active:
            raise ShareNotFoundError()
        return share
