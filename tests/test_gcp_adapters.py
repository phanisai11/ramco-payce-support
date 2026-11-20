"""Unit tests for GCP adapter modules (mocked; no credentials required)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from payce_support.gcs_store import GcsImmutableObjectStore, _OBJECT_ID_META
from payce_support.models import (
    AuthenticatedIngressContext,
    RawIntakeEnvelope,
    RawObjectPointer,
    Region,
    Source,
    TenantResolution,
    TenantResolutionMethod,
    TenantResolutionStatus,
    TenantRecord,
)
from payce_support.postgres import _record_to_manifest
from payce_support.pubsub_events import IngestEventPublisher, ticket_ingested_attributes
from payce_support.storage import ObjectAlreadyExistsError


def test_gcs_create_uses_generation_match_zero() -> None:
    from google.cloud.exceptions import PreconditionFailed

    body = b"hello"
    oid = uuid4()

    mock_blob = MagicMock()
    mock_blob.generation = 42
    mock_blob.metadata = {}
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    store = GcsImmutableObjectStore(bucket_name="b", client=mock_client)
    ref = store.create(object_id=oid, object_path="in/object", body=body)

    assert ref.object_id == oid
    assert ref.generation == "42"
    mock_blob.upload_from_string.assert_called_once()
    call_kw = mock_blob.upload_from_string.call_args.kwargs
    assert call_kw["if_generation_match"] == 0
    assert mock_blob.metadata[_OBJECT_ID_META] == str(oid)

    mock_blob.upload_from_string.side_effect = PreconditionFailed("exists")
    with pytest.raises(ObjectAlreadyExistsError):
        store.create(object_id=uuid4(), object_path="in/object", body=body)


def test_gcs_describe_reads_object_id_metadata() -> None:
    oid = uuid4()
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_blob.generation = 7
    mock_blob.metadata = {_OBJECT_ID_META: str(oid)}
    mock_blob.download_as_bytes.return_value = b"x" * 3

    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    store = GcsImmutableObjectStore(bucket_name="b", client=mock_client)
    ref = store.describe("path/obj")

    assert ref.object_id == oid
    assert ref.size_bytes == 3


def test_pubsub_publishes_pointer() -> None:
    ctx = AuthenticatedIngressContext(
        principal_id="p1",
        channel_account_id="ch1",
        ingress_region=Region.INDIA,
        authenticated_tenant_id="C-1001",
    )
    tenant = TenantRecord(
        tenant_id="C-1001",
        tenant_uuid=UUID("00000000-0000-4000-8000-000000000101"),
        region=Region.INDIA,
    )
    resolution = TenantResolution(
        status=TenantResolutionStatus.RESOLVED,
        method=TenantResolutionMethod.AUTHENTICATED_SESSION,
        mapping_version="v1",
        tenant=tenant,
    )
    sample_envelope = RawIntakeEnvelope(
        event_id=uuid4(),
        ingress_id=uuid4(),
        source=Source.EMAIL,
        source_event_id="ext-1",
        received_at=datetime.now(timezone.utc),
        source_occurred_at=None,
        authenticated_subject=ctx,
        tenant_resolution=resolution,
        payload_schema="test",
        raw_body_ref=RawObjectPointer(
            object_id=uuid4(),
            sha256="a" * 64,
            generation="1",
            size_bytes=1,
        ),
        payload={},
        attachment_receipts=(),
    )
    mock_future = MagicMock()
    mock_future.result.return_value = "msg-ids"

    mock_pub = MagicMock()
    mock_pub.publish.return_value = mock_future

    publisher = IngestEventPublisher(topic_path="projects/p/topics/t", client=mock_pub)
    oid = uuid4()
    mid = publisher.publish_envelope_pointer(envelope=sample_envelope, envelope_object_id=oid)

    assert mid == "msg-ids"
    mock_pub.publish.assert_called_once()
    data, kwargs = mock_pub.publish.call_args[0][1], mock_pub.publish.call_args[1]
    assert str(oid).encode() in data
    assert kwargs["event_type"] == "ticket.ingested"
    assert kwargs["tenant_id"] == "C-1001"


def test_ticket_ingested_attributes_unresolved_tenant() -> None:
    attrs = ticket_ingested_attributes(
        tenant_id=None,
        tenant_resolution_status="unresolved",
        region=Region.UAE,
        event_id=uuid4(),
    )
    assert "tenant_id" not in attrs
    assert attrs["tenant_resolution_status"] == "unresolved"


def test_record_to_manifest_quarantine_and_tenant() -> None:
    eid = uuid4()
    iid = uuid4()
    oid = uuid4()
    q = {
        "storage_scope": "quarantine",
        "tenant_id": None,
        "ingress_id": iid,
        "region": "IN",
        "object_id": oid,
        "object_path": "in/unresolved/x/raw-body",
        "object_generation": 3,
        "sha256": "b" * 64,
        "size_bytes": 10,
        "event_id": eid,
        "attachment_id": None,
    }
    m = _record_to_manifest(q)
    assert m.tenant_id is None
    assert m.ingress_id == iid

    t = {
        "storage_scope": "tenant",
        "tenant_id": "C-1001",
        "ingress_id": None,
        "region": "IN",
        "object_id": oid,
        "object_path": "in/tenants/u/intake/events/x/raw-body",
        "object_generation": 3,
        "sha256": "b" * 64,
        "size_bytes": 10,
        "event_id": eid,
        "attachment_id": None,
    }
    m2 = _record_to_manifest(t)
    assert m2.tenant_id == "C-1001"
    assert m2.ingress_id is None
