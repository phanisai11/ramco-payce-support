"""Pub/Sub consumer for ``ticket.ingested`` pointers → TicketEventProcessor.

The publisher (`pubsub_events.IngestEventPublisher`) sends a small JSON pointer
with ``envelope_object_id``; tenant identity comes from the verified message
attribute ``tenant_id`` (never from payload). Unresolved events carry no
``tenant_id`` attribute and are skipped — they remain in quarantine by design.

For Postgres-backed manifests, ``get_for_tenant`` requires the tenant, which is
why the attribute is mandatory; a service-role bypass would break RLS.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Mapping
from uuid import UUID

from payce_support.intake import IntakeResult
from payce_support.models import RawIntakeEnvelope
from payce_support.pipeline import PipelineResult, TicketEventProcessor
from payce_support.storage import ImmutableObjectStore, ObjectManifestRepository

_POINTER_SCHEMA = "com.payce.pubsub.ticket_ingested.pointer.v1"


class PointerFormatError(ValueError):
    pass


class TenantUnresolvedPointerError(RuntimeError):
    """Pointer lacks a resolved tenant attribute — stays in quarantine."""


class TicketIngestedConsumer:
    def __init__(
        self,
        *,
        processor: TicketEventProcessor,
        manifests: ObjectManifestRepository,
        object_store: ImmutableObjectStore,
    ) -> None:
        self._processor = processor
        self._manifests = manifests
        self._object_store = object_store

    def handle_message(
        self,
        *,
        data: bytes | str,
        attributes: Mapping[str, str] | None = None,
    ) -> PipelineResult:
        """Process one Pub/Sub message body + attributes. Idempotent-ish: the
        underlying intake is idempotent; each delivery records a new canonical
        version only when the envelope bytes differ (fingerprint-checked by
        callers upstream — see notes in IMPLEMENTATION_PHASES)."""
        pointer = self._parse_pointer(data)
        attrs = dict(attributes or {})

        tenant_id = attrs.get("tenant_id")
        if not tenant_id:
            raise TenantUnresolvedPointerError(
                "ticket.ingested pointer has no tenant_id attribute",
            )

        envelope_object_id = UUID(pointer["envelope_object_id"])
        manifest = self._manifests.get_for_tenant(
            object_id=envelope_object_id,
            tenant_id=tenant_id,
        )
        body = self._object_store.read_internal(manifest.object_path)
        envelope = RawIntakeEnvelope.model_validate_json(body)

        expected_event = pointer.get("event_id")
        if expected_event is not None and str(envelope.event_id) != expected_event:
            raise PointerFormatError("pointer event_id does not match envelope")
        if envelope.tenant_resolution.tenant is None or (
            envelope.tenant_resolution.tenant.tenant_id != tenant_id
        ):
            raise PointerFormatError("envelope tenant does not match pointer attribute")

        envelope_ref = self._object_store.describe(manifest.object_path)
        intake_result = IntakeResult(
            envelope=envelope,
            envelope_ref=envelope_ref,
            request_fingerprint=sha256(body).hexdigest(),
        )
        return self._processor.process(intake_result=intake_result)

    @staticmethod
    def _parse_pointer(data: bytes | str) -> dict[str, Any]:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise PointerFormatError("pointer is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise PointerFormatError("pointer must be a JSON object")
        if payload.get("schema") != _POINTER_SCHEMA:
            raise PointerFormatError("unexpected pointer schema")
        if "envelope_object_id" not in payload:
            raise PointerFormatError("pointer missing envelope_object_id")
        return payload
