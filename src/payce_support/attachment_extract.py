"""Per-modality attachment processors (TICKET_INTELLIGENCE_DESIGN §2).

Local stand-ins for the cloud processors: PDF text via content-stream
extraction, CSV/JSON/VTT text decoding, PNG embedded text layer (tEXt/iTXt
chunks — the local analogue of Document AI OCR), and metadata-only handling
for images without a text layer, audio recordings and binary formats.

Everything returned here is *derived* data: it is masked with ``mask_pii``
and bounded before it can influence routing, analysis or the UI. Originals
stay immutable in object storage.
"""

from __future__ import annotations

import csv
import io
import json
import re
import struct
import zipfile
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from payce_support.extraction import mask_pii

MAX_EXTRACTED_CHARS = 8000
ROUTE_SNIPPET_CHARS = 600

_TEXT_MEDIA = {
    "text/plain",
    "text/csv",
    "application/json",
    "text/vtt",
    "application/x-subrip",
    "text/markdown",
}
_SHEET_MEDIA = {
    "text/tab-separated-values",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}
_AUDIO_MEDIA_PREFIXES = ("audio/", "video/")
_IMAGE_MEDIA = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}


@dataclass(frozen=True)
class AttachmentExtraction:
    """What the modality processor produced for one attachment."""

    media_type: str
    filename: str
    processor: str
    extracted_text: str = ""
    summary: str = ""

    @property
    def route_snippet(self) -> str:
        """Bounded, masked text safe to feed deterministic routing."""
        text = self.extracted_text.strip()
        if not text:
            return ""
        return mask_pii(text[:ROUTE_SNIPPET_CHARS])


def _decode_text(body: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _extract_pdf_text(body: bytes) -> str:
    """Minimal PDF text extraction: inflate streams, pull Tj/TJ strings."""
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", body, re.S):
        raw = m.group(1)
        for candidate in (raw, _try_inflate(raw)):
            if candidate is None:
                continue
            for tm in re.finditer(
                rb"\((?:\\.|[^\\)])*\)\s*Tj|\[(.*?)\]\s*TJ", candidate, re.S
            ):
                if tm.group(1) is not None:
                    inner = tm.group(1)
                    parts = re.findall(rb"\((?:\\.|[^\\)])*\)", inner)
                    line = b"".join(_unescape_pdf(p) for p in parts)
                else:
                    line = _unescape_pdf(tm.group(0).split(b"Tj")[0])
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    chunks.append(text)
            break
    return "\n".join(chunks)


def _try_inflate(raw: bytes) -> bytes | None:
    try:
        return zlib.decompress(raw)
    except zlib.error:
        return None


def _unescape_pdf(token: bytes) -> bytes:
    m = re.match(rb"\((.*)\)\s*$", token.strip(), re.S)
    if not m:
        return b""
    inner = m.group(1)
    inner = re.sub(rb"\\([nrtbf()\\])", lambda mm: {
        b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
        b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\",
    }[mm.group(1)], inner)
    inner = re.sub(rb"\\([0-7]{1,3})", lambda mm: bytes([int(mm.group(1), 8) & 0xFF]), inner)
    return inner


def _extract_csv_text(body: bytes, *, delimiter: str = ",") -> tuple[str, str]:
    text = _decode_text(body)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not rows:
        return "", "empty CSV"
    header = rows[0]
    summary = (
        f"CSV: {max(0, len(rows) - 1)} data rows, "
        f"{len(header)} columns: {', '.join(header[:12])}"
    )
    preview = "\n".join(",".join(r[:8]) for r in rows[:6] if r)
    return preview, summary


_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _extract_xlsx_text(body: bytes) -> tuple[str, str]:
    """Read shared strings + first worksheet of an .xlsx (zip of XML)."""
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in root.findall(f"{_XLSX_NS}si"):
                    shared.append("".join(
                        t.text or "" for t in si.iter(f"{_XLSX_NS}t")
                    ))
            sheet_name = next(
                (n for n in zf.namelist()
                 if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")),
                None,
            )
            if sheet_name is None:
                return "", "XLSX: no worksheet found"
            root = ET.fromstring(zf.read(sheet_name))
            rows: list[str] = []
            for row in root.iter(f"{_XLSX_NS}row"):
                cells: list[str] = []
                for c in row.findall(f"{_XLSX_NS}c"):
                    v = c.find(f"{_XLSX_NS}v")
                    if v is None or v.text is None:
                        continue
                    if c.get("t") == "s":
                        idx = int(v.text)
                        cells.append(shared[idx] if idx < len(shared) else "")
                    else:
                        cells.append(v.text)
                if any(cells):
                    rows.append(",".join(cells))
            if not rows:
                return "", "XLSX: worksheet empty"
            header = rows[0].split(",")
            summary = (
                f"XLSX: {len(rows) - 1} data rows, "
                f"{len(header)} columns: {', '.join(header[:12])}"
            )
            preview = "\n".join(rows[:8])
            return preview, summary
    except (zipfile.BadZipFile, ET.ParseError, ValueError, IndexError) as exc:
        return "", f"XLSX: unreadable ({exc.__class__.__name__})"


def _extract_png_text(body: bytes) -> str:
    """Read tEXt/iTXt/zTXt chunks — local stand-in for screenshot OCR."""
    texts: list[str] = []
    pos = 8  # skip PNG signature
    while pos + 8 <= len(body):
        (length,) = struct.unpack(">I", body[pos:pos + 4])
        ctype = body[pos + 4:pos + 8]
        data = body[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"tEXt":
            key, _, value = data.partition(b"\x00")
            texts.append(value.decode("utf-8", errors="replace"))
        elif ctype == b"zTXt":
            key, _, rest = data.partition(b"\x00")
            if len(rest) > 1:
                try:
                    texts.append(zlib.decompress(rest[1:]).decode("utf-8", errors="replace"))
                except zlib.error:
                    pass
        elif ctype == b"iTXt":
            # keyword\0 comp_flag comp_method lang\0 translated\0 text
            rest = data.split(b"\x00", 1)[-1]
            if len(rest) < 2:
                continue
            comp_flag = rest[0]
            rest = rest[2:].split(b"\x00", 1)[-1]  # drop lang tag
            parts = rest.split(b"\x00", 1)
            if len(parts) == 2:
                raw = parts[1]
                if comp_flag == 1:
                    try:
                        raw = zlib.decompress(raw)
                    except zlib.error:
                        continue
                texts.append(raw.decode("utf-8", errors="replace"))
        elif ctype == b"IEND":
            break
    return "\n".join(t for t in texts if t.strip())


def _png_dimensions(body: bytes) -> str:
    if body[:8] == b"\x89PNG\r\n\x1a\n" and len(body) > 24:
        w, h = struct.unpack(">II", body[16:24])
        return f"{w}x{h}px"
    return "unknown"


def extract_attachment(
    *,
    body: bytes,
    filename: str,
    claimed_media_type: str | None,
    detected_media_type: str | None = None,
) -> AttachmentExtraction:
    """Route one attachment body to its modality processor."""
    media = (detected_media_type or claimed_media_type or "").split(";")[0].strip().lower()
    name = filename.lower()

    # OOXML spreadsheets are zip containers — sniffers report application/zip,
    # so the filename extension is authoritative for .xlsx/.xlsm/.xls.
    if name.endswith((".xlsx", ".xlsm")):
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif name.endswith(".xls"):
        media = "application/vnd.ms-excel"

    if not media:
        if name.endswith(".pdf"):
            media = "application/pdf"
        elif name.endswith(".csv"):
            media = "text/csv"
        elif name.endswith((".txt", ".vtt", ".srt")):
            media = "text/plain"
        elif name.endswith((".png",)):
            media = "image/png"
        elif name.endswith((".jpg", ".jpeg")):
            media = "image/jpeg"
        elif name.endswith((".wav", ".mp3", ".ogg", ".m4a")):
            media = "audio/"
        elif name.endswith((".json",)):
            media = "application/json"
        elif name.endswith((".xlsx", ".xlsm")):
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif name.endswith(".xls"):
            media = "application/vnd.ms-excel"

    try:
        if media == "application/pdf" or name.endswith(".pdf"):
            text = _extract_pdf_text(body)
            masked = mask_pii(text)[:MAX_EXTRACTED_CHARS]
            pages = body.count(b"/Type /Page") or body.count(b"/Type/Page")
            return AttachmentExtraction(
                media_type=media or "application/pdf",
                filename=filename,
                processor="pdf-text-v1",
                extracted_text=masked,
                summary=(
                    f"PDF ({len(body)} bytes, ~{max(1, pages)} pages): "
                    f"{len(text.splitlines())} text lines extracted"
                ),
            )

        if media in _TEXT_MEDIA or name.endswith((".txt", ".vtt", ".srt", ".json", ".csv")):
            if media == "text/csv" or name.endswith(".csv"):
                text, summary = _extract_csv_text(body)
            elif media == "application/json" or name.endswith(".json"):
                try:
                    data = json.loads(_decode_text(body))
                    text = json.dumps(data, indent=1)[:MAX_EXTRACTED_CHARS]
                    summary = f"JSON payload ({len(body)} bytes)"
                except json.JSONDecodeError:
                    text, summary = _decode_text(body), "JSON (unparsed)"
            else:
                text, summary = _decode_text(body), f"{media or 'text'} transcript/notes"
            masked = mask_pii(text.strip())[:MAX_EXTRACTED_CHARS]
            return AttachmentExtraction(
                media_type=media or "text/plain",
                filename=filename,
                processor="text-decode-v1",
                extracted_text=masked,
                summary=summary,
            )

        if media in _SHEET_MEDIA or name.endswith(".tsv"):
            if name.endswith(".xls") or media == "application/vnd.ms-excel":
                return AttachmentExtraction(
                    media_type=media or "application/vnd.ms-excel",
                    filename=filename,
                    processor="metadata-only",
                    summary=(
                        f"XLS binary ({len(body)} bytes) — legacy format; "
                        "save as .xlsx for text extraction"
                    ),
                )
            if name.endswith((".xlsx", ".xlsm")) or media.endswith("spreadsheetml.sheet"):
                text, summary = _extract_xlsx_text(body)
                processor = "xlsx-decode-v1"
            else:
                text, summary = _extract_csv_text(body, delimiter="\t")
                processor = "csv-decode-v1"
            return AttachmentExtraction(
                media_type=media or "text/tab-separated-values",
                filename=filename,
                processor=processor,
                extracted_text=mask_pii(text)[:MAX_EXTRACTED_CHARS],
                summary=summary,
            )

        if media == "image/png" or name.endswith(".png"):
            layer = _extract_png_text(body)
            dims = _png_dimensions(body)
            if layer.strip():
                return AttachmentExtraction(
                    media_type="image/png",
                    filename=filename,
                    processor="png-text-layer-v1",
                    extracted_text=mask_pii(layer)[:MAX_EXTRACTED_CHARS],
                    summary=f"PNG screenshot {dims} — embedded text layer read",
                )
            return AttachmentExtraction(
                media_type="image/png",
                filename=filename,
                processor="metadata-only",
                summary=f"PNG image {dims} — no text layer (OCR is a cloud processor)",
            )

        if media in _IMAGE_MEDIA or media.startswith("image/"):
            return AttachmentExtraction(
                media_type=media or "image/*",
                filename=filename,
                processor="metadata-only",
                summary=f"{media or 'image'} — OCR requires the cloud Document AI processor",
            )

        if media.startswith(_AUDIO_MEDIA_PREFIXES) or name.endswith(
            (".wav", ".mp3", ".ogg", ".m4a", ".mp4")
        ):
            return AttachmentExtraction(
                media_type=media or "audio/*",
                filename=filename,
                processor="metadata-only",
                summary=(
                    f"{media or 'audio/video'} recording ({len(body)} bytes) — "
                    "transcription is a cloud Speech-to-Text processor; "
                    "look for a companion transcript attachment"
                ),
            )

        return AttachmentExtraction(
            media_type=media or "application/octet-stream",
            filename=filename,
            processor="metadata-only",
            summary=f"{media or 'binary'} ({len(body)} bytes) — no local processor",
        )
    except Exception as exc:  # never let one attachment kill intake
        return AttachmentExtraction(
            media_type=media or "application/octet-stream",
            filename=filename,
            processor="metadata-only",
            summary=f"extraction failed: {exc.__class__.__name__}",
        )
