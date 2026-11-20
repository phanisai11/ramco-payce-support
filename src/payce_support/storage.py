"""Immutable object storage port and tenant-authorizing read broker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from payce_support.models import AccessContext, ObjectManifest, StoredObjectRef


class ObjectAlreadyExistsError(RuntimeError):
    pass


class ObjectNotFoundError(RuntimeError):
    pass


class TenantAccessDeniedError(PermissionError):
    pass


class ImmutableObjectStore(Protocol):
    def create(self, *, object_id: UUID, object_path: str, body: bytes) -> StoredObjectRef: ...

    def read_internal(self, object_path: str) -> bytes: ...

    def describe(self, object_path: str) -> StoredObjectRef: ...


class ObjectManifestRepository(Protocol):
    def append(self, manifest: ObjectManifest) -> None: ...

    def get_for_tenant(self, *, object_id: UUID, tenant_id: str) -> ObjectManifest: ...


@dataclass(frozen=True)
class _StoredObject:
    ref: StoredObjectRef
    body: bytes


class InMemoryImmutableObjectStore:
    """Test adapter mirroring GCS ifGenerationMatch=0 semantics."""

    def __init__(self) -> None:
        self._objects: dict[str, _StoredObject] = {}
        self._generation = 0

    def create(self, *, object_id: UUID, object_path: str, body: bytes) -> StoredObjectRef:
        if object_path in self._objects:
            raise ObjectAlreadyExistsError(object_path)
        self._generation += 1
        ref = StoredObjectRef(
            object_id=object_id,
            object_path=object_path,
            sha256=sha256(body).hexdigest(),
            generation=str(self._generation),
            size_bytes=len(body),
        )
        self._objects[object_path] = _StoredObject(ref=ref, body=bytes(body))
        return ref

    def read_internal(self, object_path: str) -> bytes:
        try:
            return self._objects[object_path].body
        except KeyError as exc:
            raise ObjectNotFoundError(object_path) from exc

    def describe(self, object_path: str) -> StoredObjectRef:
        try:
            return self._objects[object_path].ref
        except KeyError as exc:
            raise ObjectNotFoundError(object_path) from exc


class LocalDiskImmutableObjectStore:
    """Filesystem adapter mirroring GCS ifGenerationMatch=0 semantics.

    Used for local development so raw bodies and attachment bytes survive
    process restarts (the in-memory store does not). Objects are written
    once under ``root/object_path``; ``describe``/``read_internal`` behave
    exactly like the in-memory and GCS adapters.
    """

    def __init__(self, root: str | "Path") -> None:
        from pathlib import Path

        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._generation = 0

    def _resolve(self, object_path: str) -> "Path":
        from pathlib import Path

        candidate = (self._root / object_path.lstrip("/")).resolve()
        if self._root not in candidate.parents and candidate != self._root:
            raise TenantAccessDeniedError("object path escapes store root")
        return candidate

    def create(self, *, object_id: UUID, object_path: str, body: bytes) -> StoredObjectRef:
        from pathlib import Path

        target = self._resolve(object_path)
        if target.exists():
            raise ObjectAlreadyExistsError(object_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(body))
        # Generation mirrors write order; a content hash keeps reads honest.
        self._generation += 1
        ref = StoredObjectRef(
            object_id=object_id,
            object_path=object_path,
            sha256=sha256(body).hexdigest(),
            generation=str(self._generation),
            size_bytes=len(body),
        )
        self._ref_path(object_path).write_text(
            json.dumps(
                {
                    "object_id": str(object_id),
                    "generation": ref.generation,
                }
            )
        )
        return ref

    def _ref_path(self, object_path: str) -> "Path":
        return self._resolve(object_path + ".ref.json")

    def read_internal(self, object_path: str) -> bytes:
        target = self._resolve(object_path)
        if not target.is_file():
            raise ObjectNotFoundError(object_path)
        return target.read_bytes()

    def describe(self, object_path: str) -> StoredObjectRef:
        target = self._resolve(object_path)
        if not target.is_file():
            raise ObjectNotFoundError(object_path)
        body = target.read_bytes()
        ref_file = self._ref_path(object_path)
        object_id = UUID(int=0)
        generation = "0"
        if ref_file.is_file():
            meta = json.loads(ref_file.read_text())
            object_id = UUID(meta["object_id"])
            generation = str(meta["generation"])
        return StoredObjectRef(
            object_id=object_id,
            object_path=object_path,
            sha256=sha256(body).hexdigest(),
            generation=generation,
            size_bytes=len(body),
        )


class InMemoryObjectManifestRepository:
    def __init__(self) -> None:
        self._manifests: dict[UUID, ObjectManifest] = {}

    def append(self, manifest: ObjectManifest) -> None:
        if manifest.object_id in self._manifests:
            raise ObjectAlreadyExistsError(str(manifest.object_id))
        self._manifests[manifest.object_id] = manifest

    def get(self, object_id: UUID) -> ObjectManifest:
        try:
            return self._manifests[object_id]
        except KeyError as exc:
            raise ObjectNotFoundError(str(object_id)) from exc

    def get_for_tenant(self, *, object_id: UUID, tenant_id: str) -> ObjectManifest:
        manifest = self.get(object_id)
        if manifest.tenant_id != tenant_id:
            raise ObjectNotFoundError(str(object_id))
        return manifest


class StorageBroker:
    """The only public read path for tenant-owned object bytes."""

    def __init__(
        self,
        object_store: ImmutableObjectStore,
        manifests: ObjectManifestRepository,
    ) -> None:
        self._object_store = object_store
        self._manifests = manifests
        self.access_log: list[tuple[str, str, UUID, str]] = []

    def read(
        self,
        *,
        context: AccessContext,
        object_id: UUID,
        purpose: str,
    ) -> bytes:
        if not purpose.strip():
            raise TenantAccessDeniedError("purpose is required")

        try:
            manifest = self._manifests.get_for_tenant(
                object_id=object_id,
                tenant_id=context.tenant_id,
            )
        except ObjectNotFoundError as exc:
            raise TenantAccessDeniedError("object is not available to tenant context") from exc
        if manifest.region is not context.region:
            raise TenantAccessDeniedError("object does not belong to authenticated tenant context")

        body = self._object_store.read_internal(manifest.object_path)
        if sha256(body).hexdigest() != manifest.sha256:
            raise RuntimeError("stored object hash does not match immutable manifest")
        self.access_log.append(
            (context.principal_id, context.tenant_id, object_id, purpose),
        )
        return body
