"""Attachment DLP/malware gate — deterministic scan before canonical persistence.

Produces ``CanonicalAttachmentInput`` assessments consumed by
``build_canonical_event``. This is the v1 regex/magic-byte scanner; production
replaces it with Cloud DLP + malware scanning behind the same interface
(SAAS_TENANT_DATA_CONTRACT.md §9 file-safety gate).

Decision mapping:
- malware signature / blocked extension / macro-enabled Office → ``blocked``
- PII hits (PAN/Aadhaar/EID-like → restricted; email/phone-like → sensitive)
- otherwise → ``clean``

``processing_status``: blocked or restricted content is ``human_only``;
sensitive-but-clean content is ``redacted_ready``; the rest ``metadata_only``.
"""

from __future__ import annotations

import re
from typing import Pattern

from payce_support.canonical import CanonicalAttachmentInput
from payce_support.models import PiiClass, ProcessingStatus, ScanStatus

_EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

_BLOCKED_EXTENSIONS = frozenset(
    {
        ".exe", ".dll", ".com", ".scr", ".bat", ".cmd", ".msi", ".ps1",
        ".vbs", ".js", ".jse", ".wsf", ".lnk", ".hta",
    }
)
_MACRO_OFFICE_EXTENSIONS = frozenset({".docm", ".xlsm", ".pptm", ".docb"})

# Executable magic bytes (PE, ELF, Mach-O, Java class).
_EXECUTABLE_MAGICS = (
    b"MZ",
    b"\x7fELF",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
)

_TEXT_MEDIA_PREFIXES = (b"%PDF", b"\x89PNG", b"\xff\xd8\xff", b"PK\x03\x04", b"GIF8")

_PII_RULES: tuple[tuple[str, Pattern[str]], ...] = (
    ("restricted", re.compile(r"\b[A-Z]{5}[0-9]{4}[0-9]{2}[A-Z]{3}\b|\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b|\b784[-\s]?\d{4}[-\s]?\d{7}[-\s]?[A-Z]\b", re.IGNORECASE)),
    ("sensitive", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?<!\d)(?:\+91[-\s]?)?[6-9]\d{9}(?!\d)|\b\+971[-\s]?5\d{2}[-\s]?\d{3}[-\s]?\d{4}\b")),
)

_MAX_TEXT_SCAN_BYTES = 1_048_576


def _extension(filename: str) -> str:
    lowered = filename.lower()
    dot = lowered.rfind(".")
    return lowered[dot:] if dot != -1 else ""


def _detect_media_type(body: bytes, claimed: str | None) -> str:
    if body.startswith(b"%PDF"):
        return "application/pdf"
    if body.startswith(b"\x89PNG"):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"GIF8"):
        return "image/gif"
    if body.startswith(b"PK\x03\x04"):
        return "application/zip"
    sample = body[:512]
    if sample and all(b in b"\t\n\r" or 32 <= b < 127 or b >= 128 for b in sample):
        stripped = body.lstrip()[:16]
        if stripped.startswith((b"{", b"[")):
            return "application/json"
        return "text/plain"
    return claimed or "application/octet-stream"


def _looks_executable(body: bytes) -> bool:
    return any(body.startswith(magic) for magic in _EXECUTABLE_MAGICS)


def _pii_class_for(body: bytes) -> PiiClass:
    sample = body[:_MAX_TEXT_SCAN_BYTES]
    try:
        text = sample.decode("utf-8", errors="ignore")
    except Exception:  # pragma: no cover - decode with errors=ignore cannot raise
        return PiiClass.NONE
    if not text.strip():
        return PiiClass.NONE
    worst = PiiClass.NONE
    for level, pattern in _PII_RULES:
        if pattern.search(text):
            if level == "restricted":
                return PiiClass.RESTRICTED
            worst = PiiClass.SENSITIVE
    return worst


def scan_attachment(
    *,
    body: bytes,
    filename: str,
    claimed_media_type: str | None,
) -> CanonicalAttachmentInput:
    """Classify one attachment; never raises on content — fails closed instead."""
    ext = _extension(filename)

    if _EICAR in body or ext in _BLOCKED_EXTENSIONS or _looks_executable(body):
        return CanonicalAttachmentInput(
            detected_media_type=_detect_media_type(body, claimed_media_type),
            scan_status=ScanStatus.BLOCKED,
            pii_class=PiiClass.RESTRICTED,
            processing_status=ProcessingStatus.HUMAN_ONLY,
        )

    detected = _detect_media_type(body, claimed_media_type)
    if ext in _MACRO_OFFICE_EXTENSIONS:
        return CanonicalAttachmentInput(
            detected_media_type=detected,
            scan_status=ScanStatus.CLEAN,
            pii_class=PiiClass.RESTRICTED,
            processing_status=ProcessingStatus.HUMAN_ONLY,
        )

    pii_class = _pii_class_for(body)
    if pii_class is PiiClass.RESTRICTED:
        processing = ProcessingStatus.HUMAN_ONLY
    elif pii_class is PiiClass.SENSITIVE:
        processing = ProcessingStatus.REDACTED_READY
    else:
        processing = ProcessingStatus.METADATA_ONLY
    return CanonicalAttachmentInput(
        detected_media_type=detected,
        scan_status=ScanStatus.CLEAN,
        pii_class=pii_class,
        processing_status=processing,
    )
