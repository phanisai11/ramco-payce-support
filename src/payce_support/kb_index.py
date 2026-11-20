"""KB document indexing: markdown docs → chunks → embeddings →
``app.shared_kb_chunk`` (pgvector).

This is the local/dev twin of the GCP ``kb.synced`` pipeline: docs carry
front-matter metadata (kb_id, region, validity window) that maps directly
onto the chunk columns the retriever filters by. Re-ingesting unchanged
content is a no-op — ``snapshot_version`` is a content hash, so identical
corpora reuse the same version and ``ON CONFLICT DO NOTHING`` dedupes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from payce_support.ai_contracts import EmbeddingModel, EmbeddingRequest

EMBEDDING_MODEL_ID = "openai/text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
EMBEDDING_PROFILE = "openrouter-text-embedding-3-small-1536"
SNAPSHOT_FILE = "SNAPSHOT.json"

_CHUNK_TARGET = 1100
_CHUNK_OVERLAP = 160
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class KbDoc:
    path: Path
    slug: str
    kb_id: str
    title: str
    topic: str
    region: str  # IN | UAE | ALL
    language: str
    valid_from: datetime
    valid_to: datetime | None
    body: str


def _parse_dt(value: Any, default: datetime) -> datetime:
    if not value:
        return default
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_kb_doc(path: Path) -> KbDoc:
    raw = path.read_text(encoding="utf-8")
    m = _FRONT_MATTER.match(raw)
    meta: dict[str, Any] = {}
    body = raw
    if m:
        meta = yaml.safe_load(m.group(1)) or {}
        body = m.group(2)
    body = body.strip()
    if not body:
        raise ValueError(f"{path}: empty KB document body")
    slug = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")
    return KbDoc(
        path=path,
        slug=slug,
        kb_id=str(meta.get("kb_id") or f"kb-{slug}"),
        title=str(meta.get("title") or path.stem),
        topic=str(meta.get("topic") or slug.replace("-", "_")),
        region=str(meta.get("region") or "ALL").upper(),
        language=str(meta.get("language") or "en"),
        valid_from=_parse_dt(meta.get("valid_from"), datetime(2024, 1, 1, tzinfo=timezone.utc)),
        valid_to=(
            _parse_dt(meta.get("valid_to"), datetime(2024, 1, 1, tzinfo=timezone.utc))
            if meta.get("valid_to")
            else None
        ),
        body=body,
    )


def iter_kb_docs(docs_dir: Path) -> list[KbDoc]:
    docs = [
        load_kb_doc(p)
        for p in sorted(docs_dir.glob("*.md"))
        if not p.name.startswith(".")
    ]
    if not docs:
        raise ValueError(f"no KB documents found in {docs_dir}")
    return docs


def chunk_text(text: str, *, target: int = _CHUNK_TARGET, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Paragraph-aware greedy chunking with tail overlap."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for para in paragraphs:
        candidate = f"{cur}\n\n{para}" if cur else para
        if len(candidate) <= target or not cur:
            cur = candidate
            if len(cur) > target * 1.6 and "\n\n" in cur:
                # Oversized single paragraph — hard-split it.
                head, _, tail = cur.rpartition("\n\n")
                chunks.append(head)
                cur = tail or para
            continue
        chunks.append(cur)
        # overlap: keep the tail of the previous chunk as context prefix
        cur = cur[-overlap:] + "\n\n" + para if overlap else para
    if cur.strip():
        chunks.append(cur.strip())
    # Final safety: split anything still oversized on whitespace.
    out: list[str] = []
    for c in chunks:
        while len(c) > target * 1.8:
            cut = c.rfind(" ", 0, target)
            cut = cut if cut > target // 2 else target
            out.append(c[:cut].strip())
            c = c[cut - overlap:].strip() if overlap else c[cut:].strip()
        if c.strip():
            out.append(c.strip())
    return [c for c in out if c]


def _regions_for(doc: KbDoc) -> tuple[str, ...]:
    return ("IN", "UAE") if doc.region == "ALL" else (doc.region,)


def plan_chunks(docs: Iterable[KbDoc]) -> list[dict[str, Any]]:
    """Expand docs into chunk rows (pre-embedding)."""
    rows: list[dict[str, Any]] = []
    for doc in docs:
        for i, text in enumerate(chunk_text(doc.body), start=1):
            content = f"{doc.title}\n\n{text}"
            for region in _regions_for(doc):
                rows.append(
                    {
                        "region": region,
                        "chunk_id": f"{doc.slug}#c{i:03d}",
                        "kb_id": doc.kb_id,
                        "topic": doc.topic,
                        "language": doc.language,
                        "valid_from": doc.valid_from,
                        "valid_to": doc.valid_to,
                        "content_masked": _WS.sub(" ", content).strip(),
                    }
                )
    return rows


def snapshot_version_for(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for r in sorted(rows, key=lambda r: (r["region"], r["chunk_id"])):
        digest.update(r["content_masked"].encode())
        digest.update(b"\x00")
    return f"kbdocs-{digest.hexdigest()[:12]}"


async def ingest_kb_docs(
    *,
    docs_dir: Path,
    dsn: str,
    embedder: EmbeddingModel,
    embedding_profile: str = EMBEDDING_PROFILE,
    embedding_dimensions: int = EMBEDDING_DIMENSIONS,
) -> dict[str, Any]:
    """Embed and insert all doc chunks into ``app.shared_kb_chunk``.

    Returns stats; writes ``SNAPSHOT.json`` next to the docs so the API can
    discover the active snapshot_version + embedding_profile at startup.
    """
    import psycopg

    docs = iter_kb_docs(docs_dir)
    rows = plan_chunks(docs)
    if not rows:
        raise ValueError("KB docs produced zero chunks")
    snapshot = snapshot_version_for(rows)

    # Embed in batches (EmbeddingRequest caps at 128 texts).
    vectors: list[tuple[float, ...]] = []
    for i in range(0, len(rows), 64):
        batch = rows[i : i + 64]
        result = await embedder.embed(
            EmbeddingRequest(
                texts=tuple(r["content_masked"] for r in batch),
                input_type="search_document",
                dimensions=embedding_dimensions,
            )
        )
        vectors.extend(result.vectors)
    if len(vectors) != len(rows):
        raise RuntimeError("embedding count mismatch")

    inserted = 0
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for row, vec in zip(rows, vectors):
            lit = "[" + ",".join(format(v, ".8g") for v in vec) + "]"
            cur.execute(
                """
                INSERT INTO app.shared_kb_chunk (
                  region, snapshot_version, chunk_id, kb_id, topic,
                  language, valid_from, valid_to, content_masked,
                  content_sha256, embedding_profile, embedding
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  encode(sha256(convert_to(%s, 'UTF8')), 'hex'),
                  %s, %s::vector
                )
                ON CONFLICT (region, snapshot_version, chunk_id) DO NOTHING
                """,
                (
                    row["region"], snapshot, row["chunk_id"], row["kb_id"],
                    row["topic"], row["language"], row["valid_from"],
                    row["valid_to"], row["content_masked"],
                    row["content_masked"], embedding_profile, lit,
                ),
            )
            inserted += cur.rowcount
        conn.commit()

    manifest = {
        "snapshot_version": snapshot,
        "embedding_profile": embedding_profile,
        "embedding_model_id": embedder.model_id,
        "embedding_dimensions": embedding_dimensions,
        "docs": len(docs),
        "chunks": len(rows),
        "inserted": inserted,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    (docs_dir / SNAPSHOT_FILE).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
