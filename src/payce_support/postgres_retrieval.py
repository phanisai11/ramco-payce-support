"""Efficient pgvector adapter for region/time-scoped shared knowledge retrieval."""

from __future__ import annotations

import math
import re
from contextlib import asynccontextmanager
from typing import Any

from payce_support.ai_contracts import (
    RetrievalQuery,
    RetrievalScope,
    RetrievedChunk,
)
from payce_support.models import Region

_SHARED_KB_QUERY = """
WITH candidates AS (
  SELECT
    chunk_id,
    kb_id,
    content_masked,
    region,
    valid_from,
    valid_to,
    snapshot_version,
    GREATEST(
      0.0,
      LEAST(1.0, 1.0 - (embedding <=> %s::vector))
    ) AS score
  FROM app.shared_kb_chunk
  WHERE region = %s
    AND snapshot_version = %s
    AND embedding_profile = %s
    AND valid_from <= %s
    AND (valid_to IS NULL OR %s < valid_to)
  ORDER BY embedding <=> %s::vector
  LIMIT %s
)
SELECT
  chunk_id,
  kb_id,
  content_masked,
  score,
  region,
  valid_from,
  valid_to,
  snapshot_version
FROM candidates
WHERE score >= %s
ORDER BY score DESC, chunk_id ASC
"""


class DirectAsyncConnectionPool:
    """Minimal stand-in for ``psycopg_pool.AsyncConnectionPool``.

    Opens a fresh ``AsyncConnection`` per ``connection()`` call — satisfies
    the ``async with pool.connection()`` protocol the retriever expects
    without pool lifecycle management. Suitable for local/dev workloads.
    """

    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo

    @asynccontextmanager
    async def connection(self):
        import psycopg

        async with await psycopg.AsyncConnection.connect(
            self._conninfo
        ) as conn:
            yield conn


class PostgresKnowledgeRetriever:
    """
    Shared-KB retriever using an injected psycopg AsyncConnectionPool.

    The composition root owns pool sizing and lifecycle. The SQL performs
    region, snapshot, embedding-profile, and effective-time filtering before
    returning at most five chunks.
    """

    def __init__(self, *, pool: Any) -> None:
        self._pool = pool

    async def retrieve(
        self,
        query: RetrievalQuery,
        query_vector: tuple[float, ...],
    ) -> tuple[RetrievedChunk, ...]:
        if query.scope is not RetrievalScope.SHARED_KB:
            raise ValueError("PostgresKnowledgeRetriever supports shared KB scope only")

        vector = _vector_literal(query_vector)
        parameters = (
            vector,
            query.context.region.value,
            query.snapshot_version,
            query.embedding_profile,
            query.as_of,
            query.as_of,
            vector,
            query.top_k,
            query.minimum_score,
        )
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(_SHARED_KB_QUERY, parameters)
                rows = await cursor.fetchall()

        return tuple(
            RetrievedChunk(
                chunk_id=str(row[0]),
                kb_id=str(row[1]),
                text=str(row[2]),
                score=float(row[3]),
                region=Region(row[4]),
                valid_from=row[5],
                valid_to=row[6],
                tenant_id=None,
                snapshot_version=str(row[7]),
            )
            for row in rows
        )


_LEXICAL_CANDIDATES_QUERY = """
SELECT
  chunk_id,
  kb_id,
  content_masked,
  region,
  valid_from,
  valid_to,
  snapshot_version
FROM app.shared_kb_chunk
WHERE region = %s
  AND snapshot_version = %s
  AND embedding_profile = %s
  AND valid_from <= %s
  AND (valid_to IS NULL OR %s < valid_to)
LIMIT 500
"""

_TOKEN = re.compile(r"[a-z0-9]+")


class PostgresLexicalRetriever:
    """Keyword-overlap arm over ``app.shared_kb_chunk``.

    Fetches region/snapshot/time-filtered candidates and scores them in
    Python with the same overlap-coefficient formula as
    ``LocalKeywordRetriever`` — deterministic, no extra index needed, and
    returns chunks stamped with the real snapshot so downstream
    provenance checks pass unchanged.
    """

    def __init__(self, *, pool: Any) -> None:
        self._pool = pool

    async def retrieve(
        self,
        query: RetrievalQuery,
        query_vector: tuple[float, ...],
    ) -> tuple[RetrievedChunk, ...]:
        if query.scope is not RetrievalScope.SHARED_KB:
            raise ValueError("PostgresLexicalRetriever supports shared KB scope only")

        parameters = (
            query.context.region.value,
            query.snapshot_version,
            query.embedding_profile,
            query.as_of,
            query.as_of,
        )
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(_LEXICAL_CANDIDATES_QUERY, parameters)
                rows = await cursor.fetchall()

        q_tokens = set(_TOKEN.findall(query.text.lower()))
        scored: list[tuple[float, RetrievedChunk]] = []
        for row in rows:
            c_tokens = set(_TOKEN.findall(str(row[2]).lower()))
            if not q_tokens or not c_tokens:
                continue
            overlap = len(q_tokens & c_tokens)
            score = min(1.0, overlap / min(len(q_tokens), len(c_tokens)) * 1.2)
            if score <= 0:
                continue
            scored.append(
                (
                    score,
                    RetrievedChunk(
                        chunk_id=str(row[0]),
                        kb_id=str(row[1]),
                        text=str(row[2]),
                        score=round(score, 4),
                        region=Region(row[3]),
                        valid_from=row[4],
                        valid_to=row[5],
                        tenant_id=None,
                        snapshot_version=str(row[6]),
                    ),
                )
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        return tuple(chunk for _, chunk in scored[: query.top_k])


def _vector_literal(vector: tuple[float, ...]) -> str:
    if not 1 <= len(vector) <= 4096:
        raise ValueError("query vector dimension must be between 1 and 4096")
    if any(not math.isfinite(value) for value in vector):
        raise ValueError("query vector contains a non-finite value")
    return f"[{','.join(format(value, '.17g') for value in vector)}]"
