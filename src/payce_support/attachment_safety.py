"""Attachment safety checks for ingest (G2: zip / decompression bomb class issues).

zipfile: https://docs.python.org/3/library/zipfile.html
"""

from __future__ import annotations

import io
import zipfile

from payce_support.models import IngestCommand

# Default aligns with eval/fixtures/manifest.json `zip_max_uncompressed_bytes` until tuned by measurement.
DEFAULT_MAX_ATTACHMENT_ZIP_UNCOMPRESSED_BYTES = 1_048_576


class AttachmentSafetyError(ValueError):
    """Raised when an attachment fails policy checks."""


def total_uncompressed_zip_bytes(body: bytes) -> int | None:
    """Return total uncompressed size for members, or None if not a zip archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            return sum(zf.getinfo(name).file_size for name in zf.namelist())
    except zipfile.BadZipFile:
        return None


def validate_zip_attachment_uncompressed(
    *,
    filename: str,
    body: bytes,
    max_uncompressed_bytes: int,
) -> None:
    """Reject small-on-wire zips that expand beyond the limit (zip-bomb class)."""
    lower = filename.lower()
    if not lower.endswith(".zip"):
        return
    total = total_uncompressed_zip_bytes(body)
    if total is None:
        raise AttachmentSafetyError("attachment claims .zip but is not a valid zip archive")
    if total > max_uncompressed_bytes:
        raise AttachmentSafetyError(
            f"zip uncompressed size {total} exceeds limit {max_uncompressed_bytes}",
        )


def validate_ingest_command_attachments(
    command: IngestCommand,
    *,
    max_uncompressed_zip_bytes: int,
) -> None:
    for attachment in command.attachments:
        validate_zip_attachment_uncompressed(
            filename=attachment.original_filename,
            body=attachment.body,
            max_uncompressed_bytes=max_uncompressed_zip_bytes,
        )
