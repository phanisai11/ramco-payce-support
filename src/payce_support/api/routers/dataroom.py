"""Read-only browsing of the repository ``data-room/`` corpus.

Exposes the JSON/Markdown/CSV artifacts under ``data-room/`` — including
the detailed ``kb_docs`` Markdown sources behind KB articles — to the
dashboard. All access is constrained to the data-room directory with an
extension allowlist, a size cap, and path-traversal checks; nothing
outside it is reachable.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from payce_support.api.deps import AppStateDep, require_authenticated_v1

router = APIRouter(
    prefix="/v1/dashboard",
    tags=["dataroom"],
    dependencies=[Depends(require_authenticated_v1)],
)

_ALLOWED_SUFFIXES = {".json", ".md", ".markdown", ".csv", ".txt", ".yaml", ".yml"}
_MAX_FILE_BYTES = 512 * 1024
_MAX_LISTED_FILES = 2000


def _data_room_root(state) -> Path:
    # kb_articles_path is <root>/data-room/data/kb_articles.json
    root = Path(state.settings.kb_articles_path).resolve().parent.parent
    if root.name != "data-room":
        raise HTTPException(status_code=503, detail="data-room root unavailable")
    return root


def _safe_file(state, relative: str) -> Path:
    root = _data_room_root(state)
    candidate = (root / relative).resolve()
    if candidate.suffix.lower() not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="file type not allowed")
    if root not in candidate.parents and candidate != root:
        raise HTTPException(status_code=400, detail="path escapes data-room")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    if candidate.stat().st_size > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="file too large")
    return candidate


@router.get("/dataroom/files")
def dataroom_files(state: AppStateDep) -> dict:
    root = _data_room_root(state)
    files = []
    for path in sorted(root.rglob("*")):
        if len(files) >= _MAX_LISTED_FILES:
            break
        if not path.is_file() or path.suffix.lower() not in _ALLOWED_SUFFIXES:
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        stat = path.stat()
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    return {"root": "data-room", "files": files}


@router.get("/dataroom/file")
def dataroom_file(
    state: AppStateDep,
    path: str = Query(min_length=1, max_length=512),
) -> dict:
    target = _safe_file(state, path)
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="file is not text")
    return {
        "path": target.relative_to(_data_room_root(state)).as_posix(),
        "size": target.stat().st_size,
        "content": content,
    }


def _parse_front_matter(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta
    end = text.find("\n---", 3)
    if end == -1:
        return meta
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta


@router.get("/kb/docs")
def kb_docs(state: AppStateDep) -> dict:
    root = _data_room_root(state) / "kb_docs"
    docs = []
    if root.is_dir():
        for path in sorted(root.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            meta = _parse_front_matter(text)
            docs.append(
                {
                    "file": path.name,
                    "kb_id": meta.get("kb_id", ""),
                    "title": meta.get("title", path.stem),
                    "topic": meta.get("topic", ""),
                    "region": meta.get("region", ""),
                    "size": path.stat().st_size,
                }
            )
    return {"docs": docs}


@router.get("/kb/docs/{filename}")
def kb_doc(state: AppStateDep, filename: str) -> dict:
    # Constrain to a single *.md file directly inside kb_docs/.
    if "/" in filename or "\\" in filename or not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="invalid doc name")
    target = _data_room_root(state) / "kb_docs" / filename
    if not target.is_file():
        raise HTTPException(status_code=404, detail="doc not found")
    if target.stat().st_size > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="doc too large")
    text = target.read_text(encoding="utf-8")
    meta = _parse_front_matter(text)
    return {
        "file": filename,
        "kb_id": meta.get("kb_id", ""),
        "title": meta.get("title", filename[:-3]),
        "content": text,
    }


__all__ = ["router"]
