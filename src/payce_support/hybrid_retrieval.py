"""Hybrid retrieval: fuse a semantic (vector) arm with a lexical arm.

``HybridRetriever`` implements the ``KnowledgeRetriever`` protocol by
calling two retrievers and merging their ranked lists with Reciprocal
Rank Fusion (RRF) — the standard way to combine vector similarity and
keyword/BM25-style lexical search without needing comparable score
scales. Each arm keeps its own filters (region, snapshot, time window),
so fused results stay inside the same safety bounds.

Typical wiring: vector arm = ``PostgresKnowledgeRetriever`` (pgvector
over ``app.shared_kb_chunk``), lexical arm = ``LocalKeywordRetriever``
(token overlap over KB articles). Chunks from either corpus may appear
in the fused list; downstream validation is unchanged.
"""

from __future__ import annotations

from payce_support.ai_contracts import (
    KnowledgeRetriever,
    RetrievedChunk,
    RetrievalQuery,
)


class HybridRetriever:
    """RRF-fused retriever over two ``KnowledgeRetriever`` arms."""

    def __init__(
        self,
        *,
        vector: KnowledgeRetriever,
        keyword: KnowledgeRetriever,
        vector_weight: float = 0.6,
        keyword_weight: float = 0.4,
        rrf_k: int = 60,
    ) -> None:
        if vector_weight + keyword_weight <= 0:
            raise ValueError("arm weights must sum to a positive value")
        self._vector = vector
        self._keyword = keyword
        self._vector_weight = vector_weight
        self._keyword_weight = keyword_weight
        self._rrf_k = rrf_k

    async def retrieve(
        self,
        query: RetrievalQuery,
        query_vector: tuple[float, ...],
    ) -> tuple[RetrievedChunk, ...]:
        # Each arm may fail independently — a broken lexical arm must not
        # take down semantic retrieval (and vice versa).
        vector_chunks: tuple[RetrievedChunk, ...] = ()
        keyword_chunks: tuple[RetrievedChunk, ...] = ()
        try:
            vector_chunks = await self._vector.retrieve(query, query_vector)
        except Exception:
            pass
        try:
            keyword_chunks = await self._keyword.retrieve(query, query_vector)
        except Exception:
            pass

        scores: dict[str, float] = {}
        best: dict[str, RetrievedChunk] = {}
        for weight, ranked in (
            (self._vector_weight, vector_chunks),
            (self._keyword_weight, keyword_chunks),
        ):
            for rank, chunk in enumerate(ranked):
                # RRF: score = weight / (k + rank). Keep the arm-native
                # chunk score for downstream minimum_score filtering.
                scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + (
                    weight / (self._rrf_k + rank + 1)
                )
                # Prefer the higher arm-native score when merging dupes.
                prev = best.get(chunk.chunk_id)
                if prev is None or chunk.score > prev.score:
                    best[chunk.chunk_id] = chunk

        ordered = sorted(scores, key=lambda cid: scores[cid], reverse=True)
        return tuple(
            best[cid].model_copy(update={"score": round(best[cid].score, 4)})
            for cid in ordered[: query.top_k]
        )


__all__ = ["HybridRetriever"]
