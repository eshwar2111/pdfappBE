"""Hybrid retrieval for chat grounding and semantic dashboard search.

Dense and lexical retrieval fail in opposite directions:

* **Embeddings** capture meaning, so "what happens if I quit early?" finds a
  clause headed "Termination for convenience". They blur rare tokens, though —
  a company name like "MariApps" sits near every other proper noun, which is
  why a pure-vector search for it returns everything at a similar score.
* **Full-text ranking** is exact on rare terms and useless for paraphrase.

Running both and fusing the *rankings* covers each blind spot. Fusion is by
Reciprocal Rank Fusion rather than by blending scores, because cosine
similarity and `ts_rank_cd` are not on comparable scales — normalising them
against each other would mean inventing a conversion factor and tuning it per
corpus. RRF only needs the ordering from each retriever, so there is nothing
to calibrate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.ai.prompts import RetrievedPassage
from app.ai.provider import AIProvider, EmbeddingTask
from app.models.document_chunk import DocumentChunk
from app.repositories.chunk_repository import ChunkRepository

logger = logging.getLogger(__name__)

#: Chunks below this cosine similarity are dropped from the dense side rather
#: than padded into the prompt. Irrelevant context does not merely waste tokens
#: — it invites the model to answer from something adjacent to the question.
_MIN_SIMILARITY = 0.35

#: RRF damping. The standard value from the original paper. Larger k flattens
#: the curve, so the difference between rank 1 and rank 2 matters less and
#: agreement across retrievers matters more.
_RRF_K = 60

#: Each retriever is asked for more than the final cut so fusion has room to
#: promote a result that one side ranked mid-table and the other ranked first.
_CANDIDATE_MULTIPLIER = 3


class MatchKind(StrEnum):
    """Which retriever(s) surfaced a chunk — shown in the UI, and useful when
    explaining a ranking in the README or a walkthrough."""

    SEMANTIC = "semantic"
    KEYWORD = "keyword"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk: DocumentChunk
    #: Fused RRF score. Comparable within one result set, not across queries.
    score: float
    #: Cosine similarity, when the dense retriever found it. Kept because it is
    #: the only number here with an intuitive meaning for a user.
    similarity: float | None
    kind: MatchKind


def _fuse(
    dense: list[tuple[DocumentChunk, float]],
    lexical: list[tuple[DocumentChunk, float]],
    *,
    top_k: int,
) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion: score = Σ 1 / (k + rank) over both rankings.

    A chunk found by both retrievers accumulates two contributions and so
    outranks one found by either alone — which is the property that makes
    hybrid better than either half.
    """
    contributions: dict[UUID, float] = {}
    chunks: dict[UUID, DocumentChunk] = {}
    similarities: dict[UUID, float] = {}
    sources: dict[UUID, set[str]] = {}

    for rank, (chunk, similarity) in enumerate(dense, start=1):
        contributions[chunk.id] = contributions.get(chunk.id, 0.0) + 1.0 / (_RRF_K + rank)
        chunks[chunk.id] = chunk
        similarities[chunk.id] = similarity
        sources.setdefault(chunk.id, set()).add("dense")

    for rank, (chunk, _score) in enumerate(lexical, start=1):
        contributions[chunk.id] = contributions.get(chunk.id, 0.0) + 1.0 / (_RRF_K + rank)
        chunks[chunk.id] = chunk
        sources.setdefault(chunk.id, set()).add("lexical")

    fused = []
    for chunk_id, score in contributions.items():
        found_by = sources[chunk_id]
        kind = (
            MatchKind.BOTH
            if len(found_by) == 2
            else MatchKind.SEMANTIC
            if "dense" in found_by
            else MatchKind.KEYWORD
        )
        fused.append(
            RetrievedChunk(
                chunk=chunks[chunk_id],
                score=score,
                similarity=similarities.get(chunk_id),
                kind=kind,
            )
        )

    fused.sort(key=lambda item: item.score, reverse=True)
    return fused[:top_k]


class RetrievalService:
    def __init__(self, provider: AIProvider, chunk_repository: ChunkRepository) -> None:
        self._provider = provider
        self._chunks = chunk_repository

    async def _embed_query(self, query: str) -> list[float]:
        vectors = await self._provider.embed([query], task=EmbeddingTask.QUERY)
        return vectors[0]

    async def retrieve_for_document(
        self, *, document_id: UUID, query: str, top_k: int
    ) -> list[RetrievedChunk]:
        """Hybrid retrieval within one document, for chat grounding."""
        candidates = top_k * _CANDIDATE_MULTIPLIER

        lexical = await self._chunks.search_lexical(
            document_id=document_id, query=query, top_k=candidates
        )

        dense: list[tuple[DocumentChunk, float]] = []
        try:
            embedding = await self._embed_query(query)
            dense = [
                (chunk, similarity)
                for chunk, similarity in await self._chunks.search_by_embedding(
                    document_id=document_id,
                    query_embedding=embedding,
                    top_k=candidates,
                )
                if similarity >= _MIN_SIMILARITY
            ]
        except Exception:  # noqa: BLE001
            # Degrade to keyword-only rather than failing the whole answer: a
            # lexically grounded response beats no response.
            logger.warning("Dense retrieval unavailable; using lexical only", exc_info=True)

        return _fuse(dense, lexical, top_k=top_k)

    async def search_documents(
        self, *, document_ids: list[UUID], query: str, top_k: int
    ) -> list[RetrievedChunk]:
        """Hybrid search across a user's own documents.

        `document_ids` comes from the caller's owner-scoped listing, so both
        retrievers are confined to documents they own.
        """
        if not document_ids:
            return []

        candidates = top_k * _CANDIDATE_MULTIPLIER

        lexical = await self._chunks.search_lexical_across_documents(
            document_ids=document_ids, query=query, top_k=candidates
        )

        dense: list[tuple[DocumentChunk, float]] = []
        try:
            embedding = await self._embed_query(query)
            dense = [
                (chunk, similarity)
                for chunk, similarity in await self._chunks.search_across_documents(
                    document_ids=document_ids,
                    query_embedding=embedding,
                    top_k=candidates,
                )
                if similarity >= _MIN_SIMILARITY
            ]
        except Exception:  # noqa: BLE001
            logger.warning("Dense search unavailable; using lexical only", exc_info=True)

        return _fuse(dense, lexical, top_k=top_k)

    @staticmethod
    def to_passages(retrieved: list[RetrievedChunk]) -> list[RetrievedPassage]:
        """Order passages by position in the document, not by score.

        The model reads them as a narrative; presenting page 9 before page 2
        because it scored fractionally higher makes cross-referencing worse.
        """
        ordered = sorted(retrieved, key=lambda item: item.chunk.chunk_index)
        return [
            RetrievedPassage(
                page_start=item.chunk.page_start,
                page_end=item.chunk.page_end,
                content=item.chunk.content,
            )
            for item in ordered
        ]
