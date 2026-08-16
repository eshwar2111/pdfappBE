from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, or_, select

from app.domain.enums import DocumentStatus, ProcessingFailureReason
from app.models.document import Document
from app.repositories.base import BaseRepository

#: Trigram floor for a filename to count as a fuzzy match. Below this, results
#: are dominated by incidental character overlap.
_MIN_FILENAME_SIMILARITY = 0.15


class DocumentRepository(BaseRepository[Document]):
    model = Document

    def _owned(self, owner_id: UUID) -> Select[tuple[Document]]:
        """Every listing query starts from an owner-scoped SELECT.

        Tenant isolation is expressed once, here, rather than being remembered
        at each call site — a filter you cannot forget to apply.
        """
        return select(Document).where(Document.owner_id == owner_id)

    async def list_for_owner(
        self,
        owner_id: UUID,
        *,
        filename_query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Document], int]:
        stmt = self._owned(owner_id)
        if filename_query:
            stmt = stmt.where(Document.filename.ilike(f"%{filename_query.strip()}%"))

        total = await self.session.scalar(
            select(func.count()).select_from(stmt.subquery())
        )
        result = await self.session.execute(
            stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all()), int(total or 0)

    async def search_filenames(
        self, owner_id: UUID, query: str, *, limit: int
    ) -> list[tuple[Document, float]]:
        """Filename matching, ranked by trigram similarity.

        Substring matching alone is too brittle for filenames: "mariapps"
        would miss "MariApps_Agreement (signed).pdf" the moment a user types a
        partial or slightly-off token. pg_trgm scores character-level overlap,
        so near-misses still rank — and an exact substring hit still scores
        highest because it shares every trigram.
        """
        cleaned = query.strip()
        if not cleaned:
            return []

        similarity = func.similarity(Document.filename, cleaned)
        result = await self.session.execute(
            self._owned(owner_id)
            .add_columns(similarity.label("similarity"))
            .where(
                or_(
                    Document.filename.ilike(f"%{cleaned}%"),
                    similarity > _MIN_FILENAME_SIMILARITY,
                )
            )
            .order_by(similarity.desc(), Document.created_at.desc())
            .limit(limit)
        )
        return [(document, float(score)) for document, score in result.all()]

    async def get_owned(self, document_id: UUID, owner_id: UUID) -> Document | None:
        result = await self.session.execute(
            self._owned(owner_id).where(Document.id == document_id)
        )
        return result.scalar_one_or_none()

    async def get_many(self, document_ids: list[UUID]) -> list[Document]:
        if not document_ids:
            return []
        result = await self.session.execute(
            select(Document).where(Document.id.in_(document_ids))
        )
        return list(result.scalars().all())

    async def mark_processing(self, document: Document) -> None:
        document.status = DocumentStatus.PROCESSING
        document.failure_reason = None
        await self.session.flush()

    async def mark_ready(
        self,
        document: Document,
        *,
        summary: str,
        page_count: int,
        chunk_count: int,
    ) -> None:
        document.status = DocumentStatus.READY
        document.failure_reason = None
        document.summary = summary
        document.page_count = page_count
        document.chunk_count = chunk_count
        await self.session.flush()

    async def mark_failed(
        self, document: Document, reason: ProcessingFailureReason
    ) -> None:
        document.status = DocumentStatus.FAILED
        document.failure_reason = reason
        await self.session.flush()
