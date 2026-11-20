"""Immutable, idempotent intake orchestration."""

from __future__ import annotations

from hashlib import sha256
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict

from payce_support.attachment_safety import (
    DEFAULT_MAX_ATTACHMENT_ZIP_UNCOMPRESSED_BYTES,
    validate_ingest_command_attachments,
)
from payce_support.models import (
    AttachmentReceipt,
    AuthenticatedIngressContext,
    IngestCommand,
    ObjectManifest,
    RawIntakeEnvelope,
    RawObjectPointer,
    Region,
    StoredObjectRef,
    TenantResolutionStatus,
)
from payce_support.storage import ImmutableObjectStore, ObjectManifestRepository
from payce_support.tenancy import TenantResolver


class IdempotencyConflictError(RuntimeError):
    pass


class IntakeRepository(Protocol):
    def get(self, idempotency_key: str) -> IntakeResult | None: ...

    def append(self, idempotency_key: str, result: IntakeResult) -> None: ...


class IntakeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope: RawIntakeEnvelope
    envelope_ref: StoredObjectRef
    request_fingerprint: str


class InMemoryIntakeRepository:
    def __init__(self) -> None:
        self._results: dict[str, IntakeResult] = {}

    def get(self, idempotency_key: str) -> IntakeResult | None:
        return self._results.get(idempotency_key)

    def append(self, idempotency_key: str, result: IntakeResult) -> None:
        if idempotency_key in self._results:
            raise IdempotencyConflictError(idempotency_key)
        self._results[idempotency_key] = result


class ImmutableIntakeService:
    def __init__(
        self,
        *,
        tenant_resolver: TenantResolver,
        object_store: ImmutableObjectStore,
        manifests: ObjectManifestRepository,
        intake_repository: IntakeRepository,
        max_attachment_zip_uncompressed_bytes: int | None = (
            DEFAULT_MAX_ATTACHMENT_ZIP_UNCOMPRESSED_BYTES
        ),
    ) -> None:
        self._tenant_resolver = tenant_resolver
        self._object_store = object_store
        self._manifests = manifests
        self._intake_repository = intake_repository
        self._max_attachment_zip_uncompressed_bytes = max_attachment_zip_uncompressed_bytes

    def ingest(
        self,
        *,
        context: AuthenticatedIngressContext,
        command: IngestCommand,
    ) -> IntakeResult:
        limit = self._max_attachment_zip_uncompressed_bytes
        if limit is not None:
            validate_ingest_command_attachments(
                command,
                max_uncompressed_zip_bytes=limit,
            )

        idempotency_key = self._idempotency_key(context, command)
        request_fingerprint = self._request_fingerprint(command)
        existing = self._intake_repository.get(idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise IdempotencyConflictError(
                    "source_event_id was reused with different body or attachments",
                )
            return existing

        resolution = self._tenant_resolver.resolve(context)
        event_id = uuid5(NAMESPACE_URL, idempotency_key)
        ingress_id = uuid5(
            NAMESPACE_URL,
            f"{context.ingress_region}:{context.channel_account_id}",
        )
        base_path = self._base_path(
            context=context,
            event_id=event_id,
            ingress_id=ingress_id,
            tenant_resolution=resolution.status,
            tenant_uuid=resolution.tenant.tenant_uuid if resolution.tenant else None,
        )

        body_ref = self._store_and_manifest(
            object_id=uuid5(event_id, "raw-body"),
            object_path=f"{base_path}/raw-body",
            body=command.raw_body,
            event_id=event_id,
            tenant_id=resolution.tenant.tenant_id if resolution.tenant else None,
            region=context.ingress_region,
            ingress_id=ingress_id,
        )

        attachment_receipts: list[AttachmentReceipt] = []
        for index, attachment in enumerate(command.attachments):
            attachment_hash = sha256(attachment.body).hexdigest()
            attachment_id = uuid5(
                event_id,
                f"attachment:{index}:{attachment_hash}",
            )
            object_ref = self._store_and_manifest(
                object_id=uuid5(attachment_id, "original"),
                object_path=f"{base_path}/attachments/{attachment_id}/original",
                body=attachment.body,
                event_id=event_id,
                attachment_id=attachment_id,
                tenant_id=resolution.tenant.tenant_id if resolution.tenant else None,
                region=context.ingress_region,
                ingress_id=ingress_id,
            )
            attachment_receipts.append(
                AttachmentReceipt(
                    attachment_id=attachment_id,
                    original_filename=attachment.original_filename,
                    claimed_media_type=attachment.claimed_media_type,
                    size_bytes=len(attachment.body),
                    sha256=object_ref.sha256,
                    object_generation=object_ref.generation,
                    object_id=object_ref.object_id,
                ),
            )

        envelope = RawIntakeEnvelope(
            event_id=event_id,
            ingress_id=ingress_id,
            source=command.source,
            source_event_id=command.source_event_id,
            received_at=command.received_at,
            source_occurred_at=command.source_occurred_at,
            authenticated_subject=context,
            tenant_resolution=resolution,
            payload_schema=command.payload_schema,
            raw_body_ref=RawObjectPointer(
                object_id=body_ref.object_id,
                sha256=body_ref.sha256,
                generation=body_ref.generation,
                size_bytes=body_ref.size_bytes,
            ),
            payload=command.parsed_payload,
            attachment_receipts=tuple(attachment_receipts),
        )
        envelope_bytes = envelope.model_dump_json(by_alias=True).encode("utf-8")
        envelope_ref = self._store_and_manifest(
            object_id=uuid5(event_id, "raw-envelope"),
            object_path=f"{base_path}/raw-event.json",
            body=envelope_bytes,
            event_id=event_id,
            tenant_id=resolution.tenant.tenant_id if resolution.tenant else None,
            region=context.ingress_region,
            ingress_id=ingress_id,
        )
        result = IntakeResult(
            envelope=envelope,
            envelope_ref=envelope_ref,
            request_fingerprint=request_fingerprint,
        )
        self._intake_repository.append(idempotency_key, result)
        return result

    def _store_and_manifest(
        self,
        *,
        object_id: UUID,
        object_path: str,
        body: bytes,
        event_id: UUID,
        tenant_id: str | None,
        region: Region,
        ingress_id: UUID,
        attachment_id: UUID | None = None,
    ) -> StoredObjectRef:
        ref = self._object_store.create(
            object_id=object_id,
            object_path=object_path,
            body=body,
        )
        self._manifests.append(
            ObjectManifest(
                object_id=ref.object_id,
                tenant_id=tenant_id,
                region=region,
                object_path=ref.object_path,
                generation=ref.generation,
                sha256=ref.sha256,
                size_bytes=ref.size_bytes,
                event_id=event_id,
                attachment_id=attachment_id,
                ingress_id=None if tenant_id is not None else ingress_id,
            ),
        )
        return ref

    @staticmethod
    def _idempotency_key(
        context: AuthenticatedIngressContext,
        command: IngestCommand,
    ) -> str:
        return (
            f"{context.ingress_region}:{context.channel_account_id}:"
            f"{command.source}:{command.source_event_id}"
        )

    @staticmethod
    def _request_fingerprint(command: IngestCommand) -> str:
        digest = sha256()
        digest.update(len(command.raw_body).to_bytes(8, byteorder="big"))
        digest.update(command.raw_body)
        for attachment in command.attachments:
            digest.update(len(attachment.body).to_bytes(8, byteorder="big"))
            digest.update(attachment.body)
        return digest.hexdigest()

    @staticmethod
    def _base_path(
        *,
        context: AuthenticatedIngressContext,
        event_id: UUID,
        ingress_id: UUID,
        tenant_resolution: TenantResolutionStatus,
        tenant_uuid: UUID | None,
    ) -> str:
        region = context.ingress_region.value.lower()
        if tenant_resolution is TenantResolutionStatus.RESOLVED:
            if tenant_uuid is None:
                raise ValueError("resolved tenant must have UUID")
            return f"{region}/tenants/{tenant_uuid}/intake/events/{event_id}"
        return f"{region}/unresolved/{ingress_id}/{event_id}"
