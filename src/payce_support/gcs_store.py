"""Google Cloud Storage adapter for immutable create-only objects."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING
from uuid import UUID

from payce_support.models import StoredObjectRef
from payce_support.storage import ObjectAlreadyExistsError, ObjectNotFoundError

if TYPE_CHECKING:
    from google.cloud.storage import Bucket, Client

_OBJECT_ID_META = "x-payce-object-id"


class GcsImmutableObjectStore:
    """
    Maps logical object_path keys to gs://bucket/object_path with if_generation_match=0.

    Requires: google-cloud-storage (optional dependency group ``gcp``).
    """

    def __init__(self, *, bucket_name: str, client: Client | None = None) -> None:
        from google.cloud import storage

        self._client = client or storage.Client()
        self._bucket_name = bucket_name
        self._bucket: Bucket = self._client.bucket(bucket_name)

    def create(self, *, object_id: UUID, object_path: str, body: bytes) -> StoredObjectRef:
        from google.cloud.exceptions import PreconditionFailed

        blob = self._bucket.blob(object_path)
        blob.metadata = {**dict(blob.metadata or {}), _OBJECT_ID_META: str(object_id)}
        try:
            blob.upload_from_string(
                data=body,
                content_type="application/octet-stream",
                if_generation_match=0,
            )
        except PreconditionFailed as exc:
            raise ObjectAlreadyExistsError(object_path) from exc

        blob.reload()
        generation = blob.generation
        if generation is None:
            raise RuntimeError("GCS object missing generation after upload")

        return StoredObjectRef(
            object_id=object_id,
            object_path=object_path,
            sha256=sha256(body).hexdigest(),
            generation=str(int(generation)),
            size_bytes=len(body),
        )

    def read_internal(self, object_path: str) -> bytes:
        blob = self._bucket.blob(object_path)
        if not blob.exists():
            raise ObjectNotFoundError(object_path)
        return blob.download_as_bytes()

    def describe(self, object_path: str) -> StoredObjectRef:
        blob = self._bucket.blob(object_path)
        if not blob.exists():
            raise ObjectNotFoundError(object_path)
        blob.reload()
        generation = blob.generation
        if generation is None:
            raise RuntimeError("GCS object missing generation metadata")
        oid_raw = (blob.metadata or {}).get(_OBJECT_ID_META)
        if not oid_raw:
            raise RuntimeError(
                f"GCS object {object_path!r} missing {_OBJECT_ID_META} metadata; "
                "only objects created via GcsImmutableObjectStore are describable",
            )
        body = blob.download_as_bytes()
        return StoredObjectRef(
            object_id=UUID(oid_raw),
            object_path=object_path,
            sha256=sha256(body).hexdigest(),
            generation=str(int(generation)),
            size_bytes=len(body),
        )
