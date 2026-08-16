from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import Permission
from app.domain.principal import Principal
from app.jobs.queue import JobQueue, ProcessDocumentJob
from app.models.document import Document
from app.schemas.common import Page
from app.schemas.document import (
    DocumentDetailResponse,
    DocumentFileResponse,
    DocumentSearchResult,
    DocumentSummaryResponse,
)
from app.services.document_service import DocumentService


class DocumentController:
    def __init__(
        self,
        session: AsyncSession,
        document_service: DocumentService,
        job_queue: JobQueue,
    ) -> None:
        self._session = session
        self._documents = document_service
        self._queue = job_queue

    async def upload(
        self, *, owner_id: UUID, filename: str, content_type: str | None, data: bytes
    ) -> DocumentSummaryResponse:
        """Validate, store, commit, then queue.

        The commit happens *before* the job is enqueued so the background task
        cannot start against a row that no longer exists if the request is
        rolled back. Upload returns immediately; AI processing continues after
        the response is flushed.
        """
        self._documents.validate_upload(
            filename=filename, content_type=content_type, data=data
        )
        document = await self._documents.create(
            owner_id=owner_id, filename=filename, data=data
        )
        await self._session.commit()

        await self._queue.enqueue_document_processing(
            ProcessDocumentJob(document_id=document.id, blob_key=document.blob_key)
        )
        return DocumentSummaryResponse.model_validate(document)

    async def list_documents(
        self, *, owner_id: UUID, query: str | None, limit: int, offset: int
    ) -> Page[DocumentSummaryResponse]:
        documents, total = await self._documents.list_for_owner(
            owner_id=owner_id, query=query, limit=limit, offset=offset
        )
        return Page[DocumentSummaryResponse](
            items=[DocumentSummaryResponse.model_validate(d) for d in documents],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def search(
        self, *, owner_id: UUID, query: str, limit: int
    ) -> list[DocumentSearchResult]:
        return await self._documents.search(
            owner_id=owner_id, query=query, limit=limit
        )

    @staticmethod
    def detail(
        document: Document,
        permissions: frozenset[Permission],
        principal: Principal,
    ) -> DocumentDetailResponse:
        """Effective permissions travel with the payload so the UI renders the
        right affordances — while the server re-checks each one on the write."""
        return DocumentDetailResponse(
            **DocumentSummaryResponse.model_validate(document).model_dump(),
            chunk_count=document.chunk_count,
            permissions=sorted(permissions),
            is_owner=principal.is_user and document.owner_id == principal.id,
        )

    async def file_url(self, document: Document) -> DocumentFileResponse:
        return await self._documents.file_url(document)

    async def delete(self, document: Document) -> None:
        await self._documents.delete(document)
        await self._session.commit()
