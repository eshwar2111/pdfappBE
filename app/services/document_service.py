from __future__ import annotations

import logging
import uuid
from uuid import UUID

from app.ai.extraction import validate_pdf_magic_bytes
from app.ai.retrieval import MatchKind, RetrievalService
from app.core.config import settings
from app.core.exceptions import (
    FileTooLargeError,
    UnsupportedFileTypeError,
    ValidationError,
)
from app.domain.enums import DocumentStatus
from app.models.document import Document
from app.repositories.document_repository import DocumentRepository
from app.schemas.document import (
    DocumentFileResponse,
    DocumentSearchResult,
    DocumentSummaryResponse,
)
from app.storage.base import BlobStorage

logger = logging.getLogger(__name__)

_PDF_CONTENT_TYPE = "application/pdf"
_MAX_EXCERPT_CHARS = 240

#: Reciprocal Rank Fusion damping, matching the value used in ai/retrieval.py.
_RRF_K = 60

#: Over-fetch from each retriever so fusion has room to promote a document that
#: one retriever ranked mid-table and another ranked first.
_CANDIDATE_MULTIPLIER = 3


class DocumentService:
    def __init__(
        self,
        *,
        document_repository: DocumentRepository,
        blob_storage: BlobStorage,
        retrieval_service: RetrievalService | None = None,
    ) -> None:
        self._documents = document_repository
        self._storage = blob_storage
        self._retrieval = retrieval_service

    # --- upload ------------------------------------------------------------
    @staticmethod
    def validate_upload(*, filename: str, content_type: str | None, data: bytes) -> None:
        """Three independent checks, cheapest first.

        The filename extension and the declared MIME type both come from the
        client and are advisory. The magic-byte check is the one that actually
        decides — it is the only signal the client cannot forge.
        """
        if not data:
            raise ValidationError("The uploaded file is empty.")

        if len(data) > settings.max_upload_bytes:
            raise FileTooLargeError(
                f"Maximum upload size is "
                f"{settings.max_upload_bytes // (1024 * 1024)} MB."
            )

        if not filename.lower().endswith(".pdf"):
            raise UnsupportedFileTypeError()

        if content_type and content_type.split(";")[0].strip() != _PDF_CONTENT_TYPE:
            raise UnsupportedFileTypeError()

        validate_pdf_magic_bytes(data)

    async def create(
        self, *, owner_id: UUID, filename: str, data: bytes
    ) -> Document:
        """Persist the bytes to blob storage, then the metadata row.

        Blob first: a blob with no row is harmless garbage, while a row with no
        blob is a document that can never be opened.
        """
        blob_key = f"{owner_id}/{uuid.uuid4()}.pdf"
        await self._storage.upload(
            key=blob_key, data=data, content_type=_PDF_CONTENT_TYPE
        )

        return await self._documents.add(
            Document(
                owner_id=owner_id,
                filename=filename,
                content_type=_PDF_CONTENT_TYPE,
                size_bytes=len(data),
                blob_key=blob_key,
                status=DocumentStatus.UPLOADED,
            )
        )

    # --- read --------------------------------------------------------------
    async def file_url(self, document: Document) -> DocumentFileResponse:
        signed = await self._storage.signed_url(
            key=document.blob_key, filename=document.filename
        )
        return DocumentFileResponse(
            url=signed.url,
            expires_at=signed.expires_at,
            filename=document.filename,
        )

    async def list_for_owner(
        self, *, owner_id: UUID, query: str | None, limit: int, offset: int
    ) -> tuple[list[Document], int]:
        return await self._documents.list_for_owner(
            owner_id, filename_query=query, limit=limit, offset=offset
        )

    async def delete(self, document: Document) -> None:
        """Remove the row first; cascades clear chunks, shares and comments.

        The blob is deleted afterwards on a best-effort basis — an orphaned
        blob is recoverable waste, whereas a deleted blob behind a live row
        would be a broken document.
        """
        blob_key = document.blob_key
        await self._documents.delete(document)
        try:
            await self._storage.delete(key=blob_key)
        except Exception:  # noqa: BLE001
            logger.warning("Orphaned blob left behind: %s", blob_key, exc_info=True)

    # --- search ------------------------------------------------------------
    async def search(
        self, *, owner_id: UUID, query: str, limit: int
    ) -> list[DocumentSearchResult]:
        """Hybrid search over filenames and content.

        Three retrievers run and their *rankings* are fused with RRF:

        1. filename trigram similarity
        2. full-text (lexical) matching over chunks
        3. embedding similarity over chunks

        Fusing rankings rather than scores avoids inventing a conversion
        between trigram similarity, ``ts_rank_cd`` and cosine distance — three
        numbers on unrelated scales. A document that several retrievers agree
        on rises above one that only a single retriever liked, which is what
        makes a rare proper noun like "MariApps" rank correctly where pure
        vector search returned everything at ~60%.
        """
        cleaned = query.strip()
        if not cleaned:
            return []

        owned, _ = await self._documents.list_for_owner(owner_id, limit=500, offset=0)
        by_id = {document.id: document for document in owned}
        ready_ids = [
            document.id for document in owned if document.status is DocumentStatus.READY
        ]

        contributions: dict[UUID, float] = {}
        excerpts: dict[UUID, str] = {}
        relevance: dict[UUID, float] = {}
        matched_by: dict[UUID, set[str]] = {}
        counted: set[tuple[UUID, str]] = set()

        def contribute(document_id: UUID, rank: int, source: str) -> None:
            """Score a document by its *best* rank from each retriever.

            Only the first (highest-ranked) hit per document per source counts.
            Summing every matching chunk would rank by document length instead
            of relevance: a 13-page paper produces far more chunks than a
            5-page agreement, so it accumulates more contributions and wins
            even when the shorter document is the better match.
            """
            key = (document_id, source)
            if key in counted:
                return
            counted.add(key)

            contributions[document_id] = contributions.get(document_id, 0.0) + 1.0 / (
                _RRF_K + rank
            )
            matched_by.setdefault(document_id, set()).add(source)

        # 1. Filenames.
        filename_hits = await self._documents.search_filenames(
            owner_id, cleaned, limit=limit * _CANDIDATE_MULTIPLIER
        )
        for rank, (document, _score) in enumerate(filename_hits, start=1):
            contribute(document.id, rank, "filename")

        # 2 + 3. Content, already hybrid-fused inside the retrieval service.
        if self._retrieval is not None and ready_ids:
            try:
                retrieved = await self._retrieval.search_documents(
                    document_ids=ready_ids,
                    query=cleaned,
                    top_k=limit * _CANDIDATE_MULTIPLIER,
                )
            except Exception:  # noqa: BLE001 - search must degrade, not fail
                logger.warning(
                    "Content search unavailable; filename results only", exc_info=True
                )
                retrieved = []

            for rank, item in enumerate(retrieved, start=1):
                # A hit found only by embeddings, and only weakly, is noise on a
                # dashboard: searching "mariapps" should not list every document
                # merely because proper nouns cluster in embedding space. Hits
                # confirmed by the lexical retriever are kept regardless of
                # similarity — an exact term match is evidence in itself.
                if (
                    item.kind is MatchKind.SEMANTIC
                    and (item.similarity or 0.0) < settings.search_min_similarity
                ):
                    continue

                document_id = item.chunk.document_id
                contribute(document_id, rank, "content")
                # Keep the best-ranked passage per document as the preview.
                if document_id not in excerpts:
                    excerpts[document_id] = _excerpt(item.chunk.content)
                    if item.similarity is not None:
                        relevance[document_id] = round(item.similarity, 4)

        ranked = sorted(contributions.items(), key=lambda pair: pair[1], reverse=True)
        if not ranked:
            return []

        # Report relevance relative to the best result rather than as a raw RRF
        # score, which is a small unbounded number with no meaning on its own.
        # Deriving the percentage from the same value used for ordering
        # guarantees the displayed number and the position can never disagree —
        # a mismatch reads as a bug even when the ranking is correct.
        best_score = ranked[0][1]

        results: list[DocumentSearchResult] = []
        for document_id, score in ranked[:limit]:
            document = by_id.get(document_id)
            if document is None:
                continue
            sources = matched_by[document_id]
            results.append(
                DocumentSearchResult(
                    document=DocumentSummaryResponse.model_validate(document),
                    relevance=round(score / best_score, 4) if best_score else None,
                    excerpt=excerpts.get(document_id),
                    matched_on=(
                        "both"
                        if len(sources) == 2
                        else "filename"
                        if "filename" in sources
                        else "content"
                    ),
                )
            )

        return results


def _excerpt(content: str) -> str:
    collapsed = " ".join(content.split())
    if len(collapsed) <= _MAX_EXCERPT_CHARS:
        return collapsed
    return collapsed[:_MAX_EXCERPT_CHARS].rsplit(" ", 1)[0] + "..."
