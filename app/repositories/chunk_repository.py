from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import delete, func, select

from app.models.document_chunk import DocumentChunk
from app.repositories.base import BaseRepository

#: Postgres text-search configuration. English stemming folds "terminates" and
#: "termination" together, which is what makes lexical matching useful on prose
#: rather than only on exact strings.
_TS_CONFIG = "english"

#: Anything that is not a word character is dropped, so user input can never
#: reach `to_tsquery` as query syntax.
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class ChunkRepository(BaseRepository[DocumentChunk]):
    model = DocumentChunk

    async def bulk_add(self, chunks: list[DocumentChunk]) -> None:
        self.session.add_all(chunks)
        await self.session.flush()

    async def delete_for_document(self, document_id: UUID) -> None:
        """Called before re-ingesting so a reprocess cannot leave stale vectors
        alongside fresh ones."""
        await self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        await self.session.flush()

    async def search_by_embedding(
        self,
        *,
        document_id: UUID,
        query_embedding: list[float],
        top_k: int,
    ) -> list[tuple[DocumentChunk, float]]:
        """Nearest neighbours *within a single document*.

        The ``document_id`` predicate is part of the query, not a post-filter:
        retrieval physically cannot return a chunk the caller is not authorized
        to see. ``<=>`` is pgvector's cosine distance, so similarity is
        ``1 - distance``.
        """
        distance = DocumentChunk.embedding.cosine_distance(query_embedding)
        result = await self.session.execute(
            select(DocumentChunk, distance.label("distance"))
            .where(DocumentChunk.document_id == document_id)
            .order_by(distance)
            .limit(top_k)
        )
        return [(chunk, 1.0 - float(dist)) for chunk, dist in result.all()]

    async def search_across_documents(
        self,
        *,
        document_ids: list[UUID],
        query_embedding: list[float],
        top_k: int,
    ) -> list[tuple[DocumentChunk, float]]:
        """Semantic dashboard search, scoped to the caller's own documents."""
        if not document_ids:
            return []
        distance = DocumentChunk.embedding.cosine_distance(query_embedding)
        result = await self.session.execute(
            select(DocumentChunk, distance.label("distance"))
            .where(DocumentChunk.document_id.in_(document_ids))
            .order_by(distance)
            .limit(top_k)
        )
        return [(chunk, 1.0 - float(dist)) for chunk, dist in result.all()]

    # --- lexical (BM25-family) retrieval ------------------------------------
    def _lexical_query(self, query: str):
        """Build a prefix-matching tsquery from raw user input.

        Two things this has to get right:

        *Prefixes.* A user typing "intern" expects to find "internship".
        Stemming does not do that — `internship` stems to `internship`, not to
        `intern` — so each token is suffixed with `:*`. Search-as-you-type is
        prefix matching by nature, and without this every partial word returns
        nothing.

        *Recall over precision.* Tokens are OR-ed rather than AND-ed, because a
        query like "internship duration mariapps" would otherwise require every
        term in one chunk and match nothing. `ts_rank_cd` already ranks chunks
        matching more terms higher, and the dense retriever supplies precision
        on the other side of the fusion.

        Input is reduced to alphanumeric tokens before being handed to
        `to_tsquery`, so no user input can reach it as query syntax.
        """
        tokens = [token for token in _TOKEN_PATTERN.findall(query.lower()) if len(token) > 1]

        if not tokens:
            # Nothing usable (punctuation, or a single character). Fall back to
            # websearch_to_tsquery, which tolerates anything without raising.
            return func.websearch_to_tsquery(_TS_CONFIG, query)

        return func.to_tsquery(_TS_CONFIG, " | ".join(f"{token}:*" for token in tokens))

    async def search_lexical(
        self, *, document_id: UUID, query: str, top_k: int
    ) -> list[tuple[DocumentChunk, float]]:
        """Full-text matches within one document, ranked by `ts_rank_cd`."""
        tsquery = self._lexical_query(query)
        rank = func.ts_rank_cd(DocumentChunk.content_tsv, tsquery)

        result = await self.session.execute(
            select(DocumentChunk, rank.label("rank"))
            .where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.content_tsv.op("@@")(tsquery),
            )
            .order_by(rank.desc())
            .limit(top_k)
        )
        return [(chunk, float(score)) for chunk, score in result.all()]

    async def search_lexical_across_documents(
        self, *, document_ids: list[UUID], query: str, top_k: int
    ) -> list[tuple[DocumentChunk, float]]:
        if not document_ids:
            return []

        tsquery = self._lexical_query(query)
        rank = func.ts_rank_cd(DocumentChunk.content_tsv, tsquery)

        result = await self.session.execute(
            select(DocumentChunk, rank.label("rank"))
            .where(
                DocumentChunk.document_id.in_(document_ids),
                DocumentChunk.content_tsv.op("@@")(tsquery),
            )
            .order_by(rank.desc())
            .limit(top_k)
        )
        return [(chunk, float(score)) for chunk, score in result.all()]

    async def get_ordered(self, document_id: UUID) -> list[DocumentChunk]:
        result = await self.session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        )
        return list(result.scalars().all())
