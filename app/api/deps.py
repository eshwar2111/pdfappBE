"""FastAPI dependency graph.

Wiring lives here rather than inside routers so that a router reads as a list
of HTTP concerns and nothing else. Every object below is request-scoped and
built from the request's own session.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import BackgroundTasks, Depends, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingService
from app.ai.gemini_provider import GeminiProvider
from app.ai.provider import AIProvider
from app.ai.retrieval import RetrievalService
from app.ai.summarization import SummarizationService
from app.core.database import get_db_session
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.domain.enums import Permission
from app.domain.principal import Principal
from app.email.base import EmailSender
from app.email.factory import get_email_sender
from app.jobs.queue import BackgroundTaskQueue, JobQueue
from app.models.document import Document
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.comment_repository import CommentRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.share_repository import GuestSessionRepository, ShareRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService
from app.services.authorization_service import AuthorizationService
from app.services.chat_service import ChatService
from app.services.comment_service import CommentService
from app.services.document_service import DocumentService
from app.services.sharing_service import SharingService
from app.storage.base import BlobStorage
from app.storage.factory import get_blob_storage

# ``auto_error=False`` so a missing header raises our own typed error and
# renders through the standard error envelope like everything else.
_bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


# --- repositories ------------------------------------------------------------
def get_user_repository(session: DbSession) -> UserRepository:
    return UserRepository(session)


def get_document_repository(session: DbSession) -> DocumentRepository:
    return DocumentRepository(session)


def get_chunk_repository(session: DbSession) -> ChunkRepository:
    return ChunkRepository(session)


def get_share_repository(session: DbSession) -> ShareRepository:
    return ShareRepository(session)


def get_guest_session_repository(session: DbSession) -> GuestSessionRepository:
    return GuestSessionRepository(session)


def get_comment_repository(session: DbSession) -> CommentRepository:
    return CommentRepository(session)


def get_conversation_repository(session: DbSession) -> ConversationRepository:
    return ConversationRepository(session)


# --- infrastructure ----------------------------------------------------------
def get_ai_provider() -> AIProvider:
    return GeminiProvider()


def get_storage() -> BlobStorage:
    return get_blob_storage()


def get_job_queue(background_tasks: BackgroundTasks) -> JobQueue:
    return BackgroundTaskQueue(background_tasks)


# --- services ----------------------------------------------------------------
def get_password_reset_repository(session: DbSession) -> PasswordResetRepository:
    return PasswordResetRepository(session)


def get_email_sender_dep() -> EmailSender:
    return get_email_sender()


def get_auth_service(
    users: Annotated[UserRepository, Depends(get_user_repository)],
    resets: Annotated[PasswordResetRepository, Depends(get_password_reset_repository)],
    email_sender: Annotated[EmailSender, Depends(get_email_sender_dep)],
) -> AuthService:
    return AuthService(users, resets, email_sender)


def get_authorization_service(
    users: Annotated[UserRepository, Depends(get_user_repository)],
    documents: Annotated[DocumentRepository, Depends(get_document_repository)],
    shares: Annotated[ShareRepository, Depends(get_share_repository)],
    guests: Annotated[GuestSessionRepository, Depends(get_guest_session_repository)],
) -> AuthorizationService:
    return AuthorizationService(
        user_repository=users,
        document_repository=documents,
        share_repository=shares,
        guest_session_repository=guests,
    )


def get_retrieval_service(
    provider: Annotated[AIProvider, Depends(get_ai_provider)],
    chunks: Annotated[ChunkRepository, Depends(get_chunk_repository)],
) -> RetrievalService:
    return RetrievalService(provider, chunks)


def get_embedding_service(
    provider: Annotated[AIProvider, Depends(get_ai_provider)],
    chunks: Annotated[ChunkRepository, Depends(get_chunk_repository)],
) -> EmbeddingService:
    return EmbeddingService(provider, chunks)


def get_summarization_service(
    provider: Annotated[AIProvider, Depends(get_ai_provider)],
) -> SummarizationService:
    return SummarizationService(provider)


def get_document_service(
    documents: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
    retrieval: Annotated[RetrievalService, Depends(get_retrieval_service)],
) -> DocumentService:
    return DocumentService(
        document_repository=documents,
        blob_storage=storage,
        retrieval_service=retrieval,
    )


def get_sharing_service(
    shares: Annotated[ShareRepository, Depends(get_share_repository)],
    guests: Annotated[GuestSessionRepository, Depends(get_guest_session_repository)],
    email_sender: Annotated[EmailSender, Depends(get_email_sender_dep)],
) -> SharingService:
    return SharingService(
        share_repository=shares,
        guest_session_repository=guests,
        email_sender=email_sender,
    )


def get_comment_service(
    comments: Annotated[CommentRepository, Depends(get_comment_repository)],
) -> CommentService:
    return CommentService(comments)


def get_chat_service(
    provider: Annotated[AIProvider, Depends(get_ai_provider)],
    retrieval: Annotated[RetrievalService, Depends(get_retrieval_service)],
    conversations: Annotated[
        ConversationRepository, Depends(get_conversation_repository)
    ],
) -> ChatService:
    return ChatService(
        provider=provider,
        retrieval_service=retrieval,
        conversation_repository=conversations,
    )


# --- principals --------------------------------------------------------------
async def get_principal(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
    authorization: Annotated[AuthorizationService, Depends(get_authorization_service)],
) -> Principal:
    """The single authentication entry point.

    Registered users and invited guests both arrive here and leave as a
    ``Principal``. No route below this ever inspects a raw token.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError()
    return await authorization.resolve_principal(credentials.credentials)


async def get_current_user_principal(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """For routes that only a registered account may reach — upload, dashboard,
    share management. A guest token is rejected here by kind, not by scope."""
    if not principal.is_user:
        raise AuthorizationError("This action requires a signed-in account.")
    return principal


CurrentUser = Annotated[Principal, Depends(get_current_user_principal)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


# --- document access ---------------------------------------------------------
class DocumentAccess:
    """A document the caller has been authorized for, plus their permissions."""

    def __init__(self, document: Document, permissions: frozenset[Permission]) -> None:
        self.document = document
        self.permissions = permissions

    @property
    def is_owner_view(self) -> bool:
        return Permission.MANAGE in self.permissions


def require_document(
    permission: Permission,
) -> Callable[..., Coroutine[Any, Any, DocumentAccess]]:
    """Dependency factory: ``Depends(require_document(Permission.COMMENT))``.

    Because it is a dependency, the check runs before the handler body — there
    is no path into a protected handler that skips authorization.
    """

    async def dependency(
        document_id: Annotated[UUID, Path()],
        principal: CurrentPrincipal,
        authorization: Annotated[
            AuthorizationService, Depends(get_authorization_service)
        ],
    ) -> DocumentAccess:
        document, permissions = await authorization.authorize_document(
            principal=principal, document_id=document_id, required=permission
        )
        return DocumentAccess(document, permissions)

    return dependency


def get_frontend_base_url(request: Request) -> str:
    """Base URL used to build share links.

    Prefers the configured origin over the request's own Host header — trusting
    an attacker-controlled Host would let a poisoned link be minted.
    """
    from app.core.config import settings

    if settings.cors_origins:
        return settings.cors_origins[0]
    return str(request.base_url).rstrip("/")
