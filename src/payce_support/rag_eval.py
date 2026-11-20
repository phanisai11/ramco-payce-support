"""Offline RAG quality metrics for G5b/G5c (no Ragas dependency in default dev install).

Ragas (optional weekly job): https://docs.ragas.io/
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from payce_support.ai_contracts import RetrievedChunk

_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN.finditer(text)}


def context_precision(
    *,
    retrieved: Sequence[RetrievedChunk],
    relevant_chunk_ids: set[str],
) -> float:
    """Fraction of retrieved chunks that are in the relevant set."""
    if not retrieved:
        return 0.0
    hits = sum(1 for chunk in retrieved if chunk.chunk_id in relevant_chunk_ids)
    return hits / len(retrieved)


def answer_faithfulness(answer: str, cited_chunks: Sequence[RetrievedChunk]) -> float:
    """Lexical overlap proxy: fraction of answer tokens present in cited chunk text union."""
    if not answer.strip():
        return 0.0
    answer_toks = _tokens(answer)
    if not answer_toks:
        return 1.0
    union: set[str] = set()
    for chunk in cited_chunks:
        union |= _tokens(chunk.text)
    supported = answer_toks & union
    return len(supported) / len(answer_toks)
