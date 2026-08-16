from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile, status

from app.api.deps import (
    CurrentPrincipal,
    CurrentUser,
    DbSession,
    DocumentAccess,
    get_document_service,
    get_job_queue,
    require_document,
)
from app.controllers.document_controller import DocumentController
from app.domain.enums import Permission
from app.jobs.queue import JobQueue
from app.schemas.common import Page
from app.schemas.document import (
    DocumentDetailResponse,
    DocumentFileResponse,
    DocumentSearchResult,
    DocumentSummaryResponse,
)
from app.services.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


def get_controller(
    session: DbSession,
    document_service: Annotated[DocumentService, Depends(get_document_service)],
    job_queue: Annotated[JobQueue, Depends(get_job_queue)],
) -> DocumentController:
    return DocumentController(session, document_service, job_queue)


Controller = Annotated[DocumentController, Depends(get_controller)]


@router.post(
    "",
    response_model=DocumentSummaryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    principal: CurrentUser,
    controller: Controller,
    file: Annotated[UploadFile, File(description="A PDF file.")],
) -> DocumentSummaryResponse:
    """Upload a PDF.

    Returns as soon as the file is stored, with ``status=UPLOADED``. Extraction,
    embedding and summarisation run in the background; the client polls this
    document until it reaches READY or FAILED.
    """
    data = await file.read()
    return await controller.upload(
        owner_id=principal.id,
        filename=file.filename or "document.pdf",
        content_type=file.content_type,
        data=data,
    )


@router.get("", response_model=Page[DocumentSummaryResponse])
async def list_documents(
    principal: CurrentUser,
    controller: Controller,
    q: Annotated[str | None, Query(max_length=200, description="Filename filter.")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DocumentSummaryResponse]:
    """The dashboard listing — always scoped to the caller's own documents."""
    return await controller.list_documents(
        owner_id=principal.id, query=q, limit=limit, offset=offset
    )


@router.get("/search", response_model=list[DocumentSearchResult])
async def search_documents(
    principal: CurrentUser,
    controller: Controller,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[DocumentSearchResult]:
    """Filename matching plus embedding-based semantic search over content."""
    return await controller.search(owner_id=principal.id, query=q, limit=limit)


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(
    principal: CurrentPrincipal,
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.VIEW))],
) -> DocumentDetailResponse:
    """Reachable by the owner or by a guest holding a VIEW share link."""
    return controller.detail(access.document, access.permissions, principal)


@router.get("/{document_id}/file", response_model=DocumentFileResponse)
async def get_document_file(
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.VIEW))],
) -> DocumentFileResponse:
    """Mint a short-lived signed URL for the PDF bytes.

    The container is private; this URL is created only after authorization has
    passed and expires within minutes.
    """
    return await controller.file_url(access.document)


# response_model=None is required alongside a 204: without it FastAPI reads the
# `-> None` annotation as a NoneType response model and rejects the route.
@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_document(
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.MANAGE))],
) -> None:
    """Owner-only — MANAGE is never granted by a share link."""
    await controller.delete(access.document)
