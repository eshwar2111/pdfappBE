from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.domain.enums import DocumentStatus, Permission, ProcessingFailureReason
from app.schemas.common import APISchema


class DocumentSummaryResponse(APISchema):
    """Dashboard card payload."""

    id: UUID
    filename: str
    size_bytes: int
    page_count: int | None
    status: DocumentStatus
    failure_reason: ProcessingFailureReason | None
    summary: str | None
    created_at: datetime


class DocumentDetailResponse(DocumentSummaryResponse):
    """Viewer payload. Adds the caller's effective permissions so the UI can
    render the correct affordances — while the server still re-checks every
    one of them on the actual write."""

    chunk_count: int | None
    permissions: list[Permission]
    is_owner: bool


class DocumentFileResponse(APISchema):
    """A short-lived, signed URL to the PDF bytes.

    The blob container is private; this URL is minted per request, only after
    authorization has passed, and expires within minutes.
    """

    url: str
    expires_at: datetime
    filename: str


class DocumentSearchResult(APISchema):
    document: DocumentSummaryResponse

    #: Relevance relative to the top hit, 0–1, derived from the same fused
    #: score used for ordering — so the number always agrees with the position.
    #: Comparable within one result set, not across queries.
    relevance: float | None = None

    #: The passage that matched, for "why did this show up?" context.
    excerpt: str | None = None
    matched_on: str = Field(description="`filename`, `content`, or `both`.")
