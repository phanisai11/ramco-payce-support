"""Google Cloud Pub/Sub helpers for ticket intake notifications."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from payce_support.models import RawIntakeEnvelope, Region


def ticket_ingested_attributes(
    *,
    tenant_id: str | None,
    tenant_resolution_status: str,
    region: Region,
    event_id: UUID,
) -> dict[str, str]:
    attrs: dict[str, str] = {
        "event_type": "ticket.ingested",
        "region": region.value,
        "event_id": str(event_id),
        "tenant_resolution_status": tenant_resolution_status,
    }
    if tenant_id is not None:
        attrs["tenant_id"] = tenant_id
    return attrs


class IngestEventPublisher:
    """
    Publishes a small JSON pointer for downstream processing (not full raw payload).

    Requires: google-cloud-pubsub (optional dependency group ``gcp``).
    """

    def __init__(self, *, topic_path: str, client: Any | None = None) -> None:
        if client is None:
            from google.cloud import pubsub_v1

            self._publisher: Any = pubsub_v1.PublisherClient()
        else:
            self._publisher = client
        self._topic_path = topic_path

    def publish_envelope_pointer(
        self,
        *,
        envelope: RawIntakeEnvelope,
        envelope_object_id: UUID,
        schema_version: str = "1",
    ) -> str:
        payload = {
            "schema": "com.payce.pubsub.ticket_ingested.pointer.v1",
            "event_id": str(envelope.event_id),
            "ingress_id": str(envelope.ingress_id),
            "envelope_object_id": str(envelope_object_id),
            "tenant_resolution_status": envelope.tenant_resolution.status.value,
        }
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        tenant_id = (
            envelope.tenant_resolution.tenant.tenant_id
            if envelope.tenant_resolution.tenant
            else None
        )
        attrs = ticket_ingested_attributes(
            tenant_id=tenant_id,
            tenant_resolution_status=envelope.tenant_resolution.status.value,
            region=envelope.authenticated_subject.ingress_region,
            event_id=envelope.event_id,
        )
        attrs["schema_version"] = schema_version
        future = self._publisher.publish(self._topic_path, data, **attrs)
        return future.result()
