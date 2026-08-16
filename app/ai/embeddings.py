"""Turning chunks into persisted, searchable vectors."""

from __future__ import annotations

import logging
from uuid import UUID

from app.ai.chunking import Chunk
from app.ai.provider import AIProvider, EmbeddingTask
from app.core.exceptions import AIProviderError
from app.models.document_chunk import DocumentChunk
from app.repositories.chunk_repository import ChunkRepository

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, provider: AIProvider, chunk_repository: ChunkRepository) -> None:
        self._provider = provider
        self._chunks = chunk_repository

    async def embed_and_store(
        self, *, document_id: UUID, chunks: list[Chunk]
    ) -> int:
        """Embed every chunk and persist it. Returns the number stored.

        Existing chunks for the document are removed first, so re-processing is
        idempotent rather than additive.
        """
        if not chunks:
            return 0

        vectors = await self._provider.embed(
            [chunk.content for chunk in chunks], task=EmbeddingTask.DOCUMENT
        )
        if len(vectors) != len(chunks):
            raise AIProviderError("Embedding count did not match the chunk count.")

        expected = self._provider.embedding_dimensions
        if any(len(vector) != expected for vector in vectors):
            raise AIProviderError(
                f"Embedding provider returned vectors of unexpected width "
                f"(expected {expected}). Check GEMINI_EMBEDDING_DIMENSIONS."
            )

        await self._chunks.delete_for_document(document_id)
        await self._chunks.bulk_add(
            [
                DocumentChunk(
                    document_id=document_id,
                    chunk_index=chunk.index,
                    content=chunk.content,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    token_count=chunk.token_count,
                    embedding=vector,
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
        )
        return len(chunks)
