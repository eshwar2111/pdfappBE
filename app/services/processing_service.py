"""The document AI pipeline.

    blob -> extract -> normalise -> chunk -> embed+store -> summarise -> READY

Every failure mode lands on a typed ``ProcessingFailureReason`` so the UI can
tell the user *what* went wrong. A document that fails is never left stuck in
PROCESSING — the status machine always terminates.
"""

from __future__ import annotations

import logging

from app.ai.chunking import chunk_document
from app.ai.embeddings import EmbeddingService
from app.ai.extraction import ExtractionError, extract_text
from app.ai.summarization import SummarizationService
from app.core.exceptions import AIProviderError, StorageError
from app.domain.enums import ProcessingFailureReason
from app.models.document import Document
from app.repositories.document_repository import DocumentRepository
from app.storage.base import BlobStorage

logger = logging.getLogger(__name__)


class ProcessingService:
    def __init__(
        self,
        *,
        document_repository: DocumentRepository,
        blob_storage: BlobStorage,
        embedding_service: EmbeddingService,
        summarization_service: SummarizationService,
    ) -> None:
        self._documents = document_repository
        self._storage = blob_storage
        self._embeddings = embedding_service
        self._summaries = summarization_service

    async def process(self, document: Document) -> None:
        await self._documents.mark_processing(document)

        try:
            data = await self._storage.download(key=document.blob_key)
        except StorageError:
            logger.exception("Could not read blob for document %s", document.id)
            await self._documents.mark_failed(document, ProcessingFailureReason.UNKNOWN)
            return

        try:
            extracted = extract_text(data)
        except ExtractionError as exc:
            logger.info("Extraction failed for %s: %s", document.id, exc)
            await self._documents.mark_failed(document, exc.reason)
            return

        chunks = chunk_document(extracted)
        if not chunks:
            await self._documents.mark_failed(
                document, ProcessingFailureReason.NO_EXTRACTABLE_TEXT
            )
            return

        try:
            stored = await self._embeddings.embed_and_store(
                document_id=document.id, chunks=chunks
            )
        except AIProviderError:
            logger.exception("Embedding failed for document %s", document.id)
            await self._documents.mark_failed(
                document, ProcessingFailureReason.EMBEDDING_FAILED
            )
            return

        try:
            summary = await self._summaries.summarize(
                chunks, filename=document.filename
            )
        except (AIProviderError, ValueError):
            logger.exception("Summarisation failed for document %s", document.id)
            # Embeddings already landed, so chat will work even though the
            # dashboard card has no summary. Recorded as a distinct reason.
            await self._documents.mark_failed(
                document, ProcessingFailureReason.SUMMARY_FAILED
            )
            return

        await self._documents.mark_ready(
            document,
            summary=summary,
            page_count=extracted.page_count,
            chunk_count=stored,
        )
        logger.info(
            "Document %s ready (%s pages, %s chunks)",
            document.id,
            extracted.page_count,
            stored,
        )
