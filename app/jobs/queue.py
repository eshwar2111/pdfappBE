"""The job queue port.

Upload returns as soon as the file is stored; extraction, chunking, embedding
and summarisation happen outside the request. The API therefore never holds a
connection open for the length of an LLM pipeline, which is what keeps
concurrent uploads from starving the worker pool.

The deployed implementation is in-process (FastAPI ``BackgroundTasks``), which
is the right size for this application. The port exists so that moving to Azure
Service Bus with a separate worker process is one adapter plus one factory
line — no changes to the pipeline itself. That trade-off is documented in the
README.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProcessDocumentJob:
    """Metadata only — never the PDF bytes.

    The worker re-reads the file from blob storage. Keeping payloads small is
    what makes the in-process and out-of-process implementations equivalent.
    """

    document_id: UUID
    blob_key: str


class JobQueue(ABC):
    @abstractmethod
    async def enqueue_document_processing(self, job: ProcessDocumentJob) -> None: ...


class BackgroundTaskQueue(JobQueue):
    """In-process adapter.

    Work is handed to FastAPI's ``BackgroundTasks``, so it runs after the
    response is flushed, on the same event loop, with its own database session.
    """

    def __init__(self, background_tasks) -> None:  # noqa: ANN001 - FastAPI type
        self._background_tasks = background_tasks

    async def enqueue_document_processing(self, job: ProcessDocumentJob) -> None:
        from app.jobs.document_processor import process_document_job

        logger.info("Queued processing for document %s", job.document_id)
        self._background_tasks.add_task(process_document_job, job)
