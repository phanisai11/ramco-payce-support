"""Local in-memory KB retriever over ``data-room/data/kb_articles.json``.

Dev/demo twin of ``PostgresKnowledgeRetriever``: same ``KnowledgeRetriever``
protocol, deterministic keyword-overlap scoring instead of pgvector. Chunks
carry region/topic metadata so tenant and time scoping still apply.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from payce_support.ai_contracts import (
    ChatMessage,
    CompletionRequest,
    CompletionResult,
    EmbeddingResult,
    EmbeddingRequest,
    KnowledgeRetriever,
    MessageRole,
    RetrievedChunk,
    RetrievalQuery,
    TokenUsage,
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _article_window(art: dict[str, Any]) -> tuple[datetime, datetime | None]:
    """Effective window for an article: explicit valid_from/valid_to win;
    otherwise ``years`` maps to Jan 1 of the earliest year → Jan 1 after
    the latest (valid_to is exclusive in RAG validation)."""
    vf, vt = art.get("valid_from"), art.get("valid_to")
    if vf:
        start = datetime.fromisoformat(str(vf)).replace(tzinfo=timezone.utc)
    else:
        years = [int(y) for y in art.get("years") or [] if str(y).isdigit()]
        start = datetime(min(years) if years else 2020, 1, 1, tzinfo=timezone.utc)
    if vt:
        end = datetime.fromisoformat(str(vt)).replace(tzinfo=timezone.utc)
    elif not vf:
        years = [int(y) for y in art.get("years") or [] if str(y).isdigit()]
        end = (
            datetime(max(years) + 1, 1, 1, tzinfo=timezone.utc) if years else None
        )
    else:
        end = None
    return start, end


_PUBLISHED = {"", "published"}


class LocalKbStore:
    """Loads KB articles once; exposes them as retrieval chunks.

    Only ``status: Published`` (or unset) articles produce retrievable
    chunks — drafts/archived items stay listed but are invisible to RAG.
    """

    def __init__(
        self,
        articles_path: Path | str,
        *,
        snapshot_version: str = "kb-local-v1",
        articles_loader: Any = None,
    ) -> None:
        self.path = Path(articles_path)
        self.snapshot_version = snapshot_version
        self._loader = articles_loader
        self._articles: list[dict[str, Any]] = []
        self._chunks: list[RetrievedChunk] = []
        self.reload()

    def reload(self) -> None:
        """Re-read articles (DB when a loader is configured, else the JSON
        file) and rebuild chunks in place — the retriever holds a reference
        to this store, so RAG sees changes immediately."""
        from payce_support.models import Region

        if self._loader is not None:
            self._articles = list(self._loader())
        else:
            self._articles = json.loads(self.path.read_text(encoding="utf-8"))
        chunks: list[RetrievedChunk] = []
        for art in self._articles:
            if str(art.get("status", "Published")).strip().lower() not in _PUBLISHED:
                continue
            region_raw = str(art.get("region", "ALL")).upper()
            regions = (
                (Region.INDIA, Region.UAE)
                if region_raw == "ALL"
                else (Region(region_raw),)
            )
            valid_from, valid_to = _article_window(art)
            text = " ".join(
                str(art.get(k, ""))
                for k in ("title", "topic", "excerpt")
            )
            for region in regions:
                chunks.append(
                    RetrievedChunk(
                        chunk_id=f"{art['id']}:{region.value}",
                        kb_id=str(art.get("topic", "general")),
                        text=f"{art.get('title','')}. {art.get('excerpt','')}",
                        score=0.0,
                        region=region,
                        valid_from=valid_from,
                        valid_to=valid_to,
                        tenant_id=None,
                        snapshot_version=self.snapshot_version,
                    )
                )
        self._chunks = chunks
        # keep raw text for scoring
        self._texts = [c.text for c in self._chunks]
        self._regions = [c.region.value for c in self._chunks]
        self._chunk_objs = self._chunks

    def articles(self) -> list[dict[str, Any]]:
        return list(self._articles)

    def chunks(self) -> tuple[RetrievedChunk, ...]:
        return tuple(self._chunk_objs)

    def _save_articles(self, articles: list[dict[str, Any]]) -> None:
        self.path.write_text(
            json.dumps(articles, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def add_article(self, article: dict[str, Any]) -> None:
        """Append an article (file mode) and hot-reload chunks."""
        articles = self.articles()
        if any(a.get("id") == article.get("id") for a in articles):
            raise KeyError(f"KB article {article.get('id')!r} already exists")
        articles.append(article)
        self._save_articles(articles)
        self.reload()

    def update_article(self, article_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Merge ``fields`` into an existing article (file mode)."""
        articles = self.articles()
        for art in articles:
            if art.get("id") == article_id:
                art.update(fields)
                self._save_articles(articles)
                self.reload()
                return art
        raise KeyError(f"KB article {article_id!r} not found")


_KB_COLUMNS = (
    "article_id, title, topic, region, excerpt, valid_from, valid_to, years,"
    " status, owner, last_reviewed, helpful_pct, views_90d"
)


def load_kb_articles_from_db(dsn: str) -> list[dict[str, Any]]:
    """Load all KB articles from ``app.kb_article`` (Postgres source of truth)."""
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_KB_COLUMNS} FROM app.kb_article ORDER BY article_id")
        rows = cur.fetchall()
    out = []
    for (
        aid, title, topic, region, excerpt, vf, vt, years,
        status, owner, reviewed, helpful, views,
    ) in rows:
        art: dict[str, Any] = {
            "id": aid,
            "title": title,
            "topic": topic,
            "region": region,
            "excerpt": excerpt,
            "status": status,
        }
        if vf:
            art["valid_from"] = vf.isoformat()
        if vt:
            art["valid_to"] = vt.isoformat()
        if years:
            art["years"] = [int(y) for y in years]
        if owner:
            art["owner"] = owner
        if reviewed:
            art["last_reviewed"] = reviewed.isoformat()
        if helpful is not None:
            art["helpful_pct"] = int(helpful)
        if views is not None:
            art["views_90d"] = int(views)
        out.append(art)
    return out


def upsert_kb_article_db(dsn: str, article: dict[str, Any]) -> None:
    """Insert or update one article in ``app.kb_article``."""
    import psycopg

    cols = [
        "article_id", "title", "topic", "region", "excerpt",
        "valid_from", "valid_to", "years", "status", "owner",
        "last_reviewed", "helpful_pct", "views_90d",
    ]
    aid = article.get("id")
    if not aid:
        raise ValueError("article id is required")
    params = [
        aid,
        article.get("title") or "",
        article.get("topic") or "general",
        article.get("region") or "ALL",
        article.get("excerpt") or "",
        article.get("valid_from") or None,
        article.get("valid_to") or None,
        list(article.get("years") or []) or None,
        article.get("status") or "Published",
        article.get("owner"),
        article.get("last_reviewed") or None,
        article.get("helpful_pct"),
        article.get("views_90d"),
    ]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "article_id")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO app.kb_article ({', '.join(cols)})"
            f" VALUES ({', '.join(['%s'] * len(cols))})"
            f" ON CONFLICT (article_id) DO UPDATE SET {updates},"
            " updated_at = now()",
            params,
        )
        conn.commit()


class LocalKeywordRetriever:
    """Deterministic retriever: token-overlap score, region filter, top_k."""

    def __init__(self, store: LocalKbStore) -> None:
        self._store = store

    async def retrieve(
        self,
        query: RetrievalQuery,
        query_vector: tuple[float, ...],
    ) -> tuple[RetrievedChunk, ...]:
        q_tokens = set(_TOKEN.findall(query.text.lower()))
        scored: list[tuple[float, RetrievedChunk]] = []
        for chunk, region in zip(self._store._chunk_objs, self._store._regions):
            if region != query.context.region.value:
                continue
            c_tokens = set(_TOKEN.findall(chunk.text.lower()))
            if not q_tokens or not c_tokens:
                continue
            overlap = len(q_tokens & c_tokens)
            # Overlap coefficient: robust for short KB chunks vs long
            # customer emails (plain query-token ratio under-scores).
            score = min(1.0, overlap / min(len(q_tokens), len(c_tokens)) * 1.2)
            if score <= 0:
                continue
            scored.append(
                (
                    score,
                    chunk.model_copy(
                        update={
                            "score": round(score, 4),
                            "valid_from": chunk.valid_from,
                        }
                    ),
                )
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        return tuple(c for _, c in scored[: query.top_k])


class LocalHashEmbedding:
    """Deterministic stand-in embedding (bag-of-words hash vector).

    Only used so ``RagComposer`` can run without an embedding provider; the
    local retriever scores by token overlap and ignores the vector.
    """

    model_id = "local-hash-embed-v1"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        dim = 64
        vectors = []
        for text in request.texts:
            vec = [0.0] * dim
            for tok in _TOKEN.findall(text.lower()):
                vec[hash(tok) % dim] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append(tuple(v / norm for v in vec))
        return EmbeddingResult(vectors=tuple(vectors), model_id=self.model_id)


class LocalExtractiveLanguageModel:
    """Deterministic ``LanguageModel`` fallback for local demo.

    Parses the RAG compose prompt and returns an extractive "answer" built
    from the retrieved chunk texts, citing every chunk used. No network
    calls — lets the full draft pipeline run without an OpenRouter key.
    """

    model_id = "local-extractive-v1"

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        # Last user message carries the JSON compose payload.
        user_msg: ChatMessage | None = None
        for msg in reversed(request.messages):
            if msg.role is MessageRole.USER:
                user_msg = msg
                break
        chunks: list[dict[str, Any]] = []
        if user_msg and user_msg.content:
            try:
                payload = json.loads(user_msg.content)
                chunks = list(payload.get("knowledge_chunks", ()))
            except (json.JSONDecodeError, AttributeError):
                chunks = []
        if not chunks:
            return CompletionResult(
                text=json.dumps({"answer": "", "citations": []}),
                model_id=self.model_id,
                usage=TokenUsage(input_tokens=0, output_tokens=0),
            )
        parts = []
        citations = []
        for chunk in chunks[:5]:
            cid = str(chunk.get("chunk_id", ""))
            text = str(chunk.get("text", "")).strip()
            if cid and text:
                citations.append(cid)
                parts.append(f"• {text}")
        answer = (
            "Based on the Payce knowledge base:\n\n"
            + "\n".join(parts)
            + "\n\nIf this does not resolve your issue, our support team can "
            "assist further."
        )
        return CompletionResult(
            text=json.dumps({"answer": answer, "citations": citations}),
            model_id=self.model_id,
            usage=TokenUsage(
                input_tokens=len(user_msg.content or "") // 4 if user_msg else 0,
                output_tokens=len(answer) // 4,
            ),
        )
