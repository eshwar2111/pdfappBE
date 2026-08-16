"""Entry point for the background processing job.

Runs after the upload response has been sent, on its own database session —
the request's session is already closed by then, so reusing it would be a
use-after-free. This function owns its transaction and swallows nothing
silently: any unexpected failure is recorded on the document row.
"""

from __future__ import annotations

import logging

from app.ai.embeddings import EmbeddingService
from app.ai.gemini_provider import GeminiProvider
from app.ai.summarization import SummarizationService
from app.core.database import SessionFactory
from app.domain.enums import ProcessingFailureReason
from app.jobs.queue import ProcessDocumentJob
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.services.processing_service import ProcessingService
from app.storage.factory import get_blob_storage

logger = logging.getLogger(__name__)


async def process_document_job(job: ProcessDocumentJob) -> None:
    async with SessionFactory() as session:
        documents = DocumentRepository(session)
        document = await documents.get(job.document_id)
        if document is None:
            logger.warning("Document %s vanished before processing", job.document_id)
            return

        provider = GeminiProvider()
        service = ProcessingService(
            document_repository=documents,
            blob_storage=get_blob_storage(),
            embedding_service=EmbeddingService(provider, ChunkRepository(session)),
            summarization_service=SummarizationService(provider),
        )

        try:
            await service.process(document)
            await session.commit()
        except Exception:  # noqa: BLE001 - last line of defence
            logger.exception("Unhandled error processing document %s", job.document_id)
            await session.rollback()
            # Roll the status forward to FAILED in a clean transaction so the
            # document does not sit in PROCESSING forever.
            try:
                document = await documents.get(job.document_id)
                if document is not None:
                    await documents.mark_failed(
                        document, ProcessingFailureReason.UNKNOWN
                    )
                    await session.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Could not record failure for %s", job.document_id)
