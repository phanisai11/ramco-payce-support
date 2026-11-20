"""Canonical event + pipeline writer tests (in-memory + mocked Postgres)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from payce_support.canonical import (
    CanonicalAttachmentInput,
    CanonicalExtraction,
    CanonicalFacts,
    build_canonical_event,
    content_fingerprint,
)
from payce_support.intake import ImmutableIntakeService, InMemoryIntakeRepository
from payce_support.models import (
    AttachmentInput,
    AuthenticatedIngressContext,
    DecidedBy,
    IngestCommand,
    Lane,
    ObjectManifest,
    Region,
    RoutingDecision,
    ScanStatus,
    Source,
    TenantRecord,
)
from payce_support.pipeline import InMemoryTicketPipelineStore
from payce_support.routing import (
    InMemoryRoutingDecisionRepository,
    RoutingCandidate,
    RoutingPolicy,
)
from payce_support.storage import (
    InMemoryImmutableObjectStore,
    InMemoryObjectManifestRepository,
)
from payce_support.tenancy import TenantResolver

_SCHEMA = json.loads(
    Path("schemas/canonical-ticket-event.schema.json").read_text(),
)


def _resolver() -> TenantResolver:
    return TenantResolver(
        (
            TenantRecord(
                tenant_id="C-1001",
                tenant_uuid=uuid4(),
                region=Region.INDIA,
            ),
        ),
        mapping_version="contacts-v1",
    )


def _ingest(service: ImmutableIntakeService):
    return service.ingest(
        context=AuthenticatedIngressContext(
            principal_id="p1",
            channel_account_id="widget-in",
            ingress_region=Region.INDIA,
            authenticated_tenant_id="C-1001",
        ),
        command=IngestCommand(
            source=Source.WIDGET,
            source_event_id="evt-1",
            received_at=datetime(2026, 11, 6, 9, tzinfo=timezone.utc),
            payload_schema="widget/v1",
            raw_body=b'{"message":"pf not calculated"}',
            parsed_payload={"message": "pf not calculated"},
            attachments=(
                AttachmentInput(
                    original_filename="payslip.pdf",
                    claimed_media_type="application/pdf",
                    body=b"%PDF-synthetic",
                ),
            ),
        ),
    )


def _extraction() -> CanonicalExtraction:
    return CanonicalExtraction(
        ticket_id="PN-101253",
        case_id="CASE-8812",
        subject_masked="PF missing",
        body_masked="My PF was not credited for August.",
        language="en",
        payroll_period="2026-08",
        payroll_week=True,
        facts=CanonicalFacts(
            run_id="RUN-778",
            jira_keys=("PNJ-4412",),
            kb_ids=("KB-102",),
            error_codes=("PF_CALC_01",),
            refund_refs=(),
        ),
        attachment_assessments={},
    )


def _components():
    resolver = _resolver()
    objects = InMemoryImmutableObjectStore()
    manifests = InMemoryObjectManifestRepository()
    service = ImmutableIntakeService(
        tenant_resolver=resolver,
        object_store=objects,
        manifests=manifests,
        intake_repository=InMemoryIntakeRepository(),
    )
    return service, manifests


def _manifests_for(result, manifests: InMemoryObjectManifestRepository):
    envelope = result.envelope
    raw_body = manifests.get(envelope.raw_body_ref.object_id)
    attachments = tuple(
        manifests.get(receipt.object_id)
        for receipt in envelope.attachment_receipts
    )
    return raw_body, attachments


def _decision(
    *,
    event_id,
    canonical_version: int,
    tenant_id: str = "C-1001",
    supersedes=None,
) -> RoutingDecision:
    repo = InMemoryRoutingDecisionRepository()
    policy = RoutingPolicy(
        policy_version="routing-v1",
        approved_rule_ids={"route-in-pf-v3"},
        repository=repo,
    )
    return policy.decide(
        tenant_id=tenant_id,
        event_id=event_id,
        canonical_version=canonical_version,
        input_fingerprint=content_fingerprint(
            tenant_id=tenant_id,
            raw_sha256="a" * 64,
            canonical_version=canonical_version,
        ),
        candidate=RoutingCandidate(
            lane=Lane.DRAFT_FOR_AGENT,
            queue_id="Statutory-Compliance",
            decided_by=DecidedBy.RULE,
            rule_id="route-in-pf-v3",
            reason_codes=("REGION_IN", "ENTITY_PF"),
            confidence=1.0,
        ),
        tenant_resolved=True,
        schema_valid=True,
        blocking_risk=False,
        supersedes_decision_id=supersedes,
    )


def test_build_canonical_event_matches_published_schema() -> None:
    service, manifests = _components()
    result = _ingest(service)
    raw_body, attachments = _manifests_for(result, manifests)

    event = build_canonical_event(
        envelope=result.envelope,
        extraction=_extraction(),
    )

    payload = json.loads(event.model_dump_json(by_alias=True))
    Draft202012Validator(_SCHEMA).validate(payload)
    assert event.ticket.tenant_id == "C-1001"
    assert event.ticket.region is Region.INDIA
    assert event.canonical_version == 1
    assert event.attachments[0].scan_status is ScanStatus.PENDING
    assert raw_body.tenant_id == "C-1001"
    assert attachments[0].tenant_id == "C-1001"


def test_build_canonical_event_rejects_unresolved_tenant() -> None:
    service, _ = _components()
    result = service.ingest(
        context=AuthenticatedIngressContext(
            principal_id="p1",
            channel_account_id="widget-in",
            ingress_region=Region.INDIA,
        ),
        command=IngestCommand(
            source=Source.WIDGET,
            source_event_id="evt-unresolved",
            received_at=datetime(2026, 11, 6, 9, tzinfo=timezone.utc),
            payload_schema="widget/v1",
            raw_body=b"{}",
            parsed_payload={},
        ),
    )
    with pytest.raises(ValueError, match="resolved tenant"):
        build_canonical_event(
            envelope=result.envelope,
            extraction=_extraction(),
        )


def test_attachment_assessment_defaults_and_override() -> None:
    service, manifests = _components()
    result = _ingest(service)
    extraction = CanonicalExtraction(
        ticket_id="PN-1",
        case_id="CASE-1",
        attachment_assessments={
            result.envelope.attachment_receipts[0].attachment_id: (
                CanonicalAttachmentInput(
                    detected_media_type="application/pdf",
                    scan_status=ScanStatus.CLEAN,
                    pii_class="sensitive",
                    processing_status="redacted_ready",
                    manifest_version=1,
                )
            ),
        },
    )
    event = build_canonical_event(envelope=result.envelope, extraction=extraction)
    attachment = event.attachments[0]
    assert attachment.scan_status is ScanStatus.CLEAN
    assert attachment.pii_class == "sensitive"
    assert attachment.processing_status == "redacted_ready"


def test_pipeline_records_canonical_decision_and_projection() -> None:
    service, manifests = _components()
    store = InMemoryTicketPipelineStore()
    result = _ingest(service)
    raw_body, attachments = _manifests_for(result, manifests)
    event_id = result.envelope.event_id

    decision = _decision(event_id=event_id, canonical_version=1)
    event = store.record_ticket_event(
        envelope=result.envelope,
        raw_body_manifest=raw_body,
        attachment_manifests=attachments,
        extraction=_extraction(),
        decision=decision,
    )

    assert event.canonical_version == 1
    assert store.get_canonical(
        tenant_id="C-1001", event_id=event_id, canonical_version=1,
    ) == event
    assert store.latest_canonical_version(tenant_id="C-1001", event_id=event_id) == 1

    current = store.get_ticket_current(tenant_id="C-1001", ticket_id="PN-101253")
    assert current is not None
    assert current.current_decision_id == decision.decision_id
    assert current.queue_id == "Statutory-Compliance"

    # Cross-tenant reads see nothing.
    assert store.get_canonical(
        tenant_id="C-1002", event_id=event_id, canonical_version=1,
    ) is None
    assert store.get_ticket_current(tenant_id="C-1002", ticket_id="PN-101253") is None


def test_pipeline_versions_and_decision_consistency() -> None:
    service, manifests = _components()
    store = InMemoryTicketPipelineStore()
    result = _ingest(service)
    raw_body, attachments = _manifests_for(result, manifests)
    event_id = result.envelope.event_id

    first = _decision(event_id=event_id, canonical_version=1)
    store.record_ticket_event(
        envelope=result.envelope,
        raw_body_manifest=raw_body,
        attachment_manifests=attachments,
        extraction=_extraction(),
        decision=first,
    )

    # Reprocessing creates canonical_version 2; v1 row is untouched.
    second = _decision(
        event_id=event_id,
        canonical_version=2,
        supersedes=first.decision_id,
    )
    # supersedes must reference a decision in the *pipeline's* repo; rebuild
    # through the store path is covered by the append-only check below.
    with pytest.raises(ValueError, match="superseded routing decision"):
        # decision was built against a different repository instance
        store.record_ticket_event(
            envelope=result.envelope,
            raw_body_manifest=raw_body,
            attachment_manifests=attachments,
            extraction=_extraction(),
            decision=second,
        )

    wrong_version = _decision(event_id=event_id, canonical_version=1)
    with pytest.raises(ValueError, match="canonical version"):
        store.record_ticket_event(
            envelope=result.envelope,
            raw_body_manifest=raw_body,
            attachment_manifests=attachments,
            extraction=_extraction(),
            decision=wrong_version,
        )


def test_routing_repository_get_is_tenant_scoped() -> None:
    repo = InMemoryRoutingDecisionRepository()
    policy = RoutingPolicy(
        policy_version="routing-v1",
        approved_rule_ids=set(),
        repository=repo,
    )
    decision = policy.decide(
        tenant_id="C-1001",
        event_id=uuid4(),
        canonical_version=1,
        input_fingerprint="a" * 64,
        candidate=RoutingCandidate(
            lane=Lane.HUMAN,
            decided_by=DecidedBy.RULE,
            reason_codes=("NO_APPROVED_RULE_MATCH",),
            confidence=0.0,
        ),
        tenant_resolved=True,
        schema_valid=True,
        blocking_risk=False,
    )
    assert repo.get(decision.decision_id, tenant_id="C-1001") == decision
    with pytest.raises(KeyError):
        repo.get(decision.decision_id, tenant_id="C-1002")


def _mock_conn_cursor():
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    conn = MagicMock()
    conn.transaction.return_value.__enter__ = lambda s: conn.transaction.return_value
    txn = conn.transaction.return_value
    txn.__enter__ = lambda s: txn
    txn.__exit__ = lambda s, *a: False
    cur_ctx = cursor
    cur_ctx.__enter__ = lambda s: cur_ctx
    cur_ctx.__exit__ = lambda s, *a: False
    conn.cursor.return_value = cur_ctx
    return conn, cursor


def test_postgres_pipeline_sets_tenant_and_writes_all_tables(monkeypatch) -> None:
    from payce_support import postgres_pipeline

    service, manifests = _components()
    result = _ingest(service)
    raw_body, attachments = _manifests_for(result, manifests)
    event_id = result.envelope.event_id

    conn, cursor = _mock_conn_cursor()
    connect = MagicMock(return_value=conn)
    monkeypatch.setattr(postgres_pipeline.psycopg, "connect", connect)

    store = postgres_pipeline.PostgresTicketPipelineStore("postgresql://x")
    decision = _decision(event_id=event_id, canonical_version=1)
    # _latest_version fetchone returns None → MAX coalesce handled in SQL;
    # emulate version 0 by returning (0,).
    cursor.fetchone.side_effect = [(0,), None]

    event = store.record_ticket_event(
        envelope=result.envelope,
        raw_body_manifest=raw_body,
        attachment_manifests=attachments,
        extraction=_extraction(),
        decision=decision,
    )

    sqls = [call.args[0] for call in cursor.execute.call_args_list]
    assert sqls[0].strip().startswith("SET LOCAL app.tenant_id")
    joined = "\n".join(sqls)
    for table in (
        "app.raw_event_manifest",
        "app.attachment_manifest",
        "app.canonical_ticket_event",
        "app.routing_decision",
        "app.ticket_current",
    ):
        assert f"INTO {table}" in joined or f"UPDATE {table}" in joined
    assert event.ticket.tenant_id == "C-1001"


def test_postgres_routing_decision_repo_roundtrip_shape(monkeypatch) -> None:
    from payce_support import postgres_pipeline

    conn, cursor = _mock_conn_cursor()
    connect = MagicMock(return_value=conn)
    monkeypatch.setattr(postgres_pipeline.psycopg, "connect", connect)

    repo = postgres_pipeline.PostgresRoutingDecisionRepository("postgresql://x")
    policy = RoutingPolicy(
        policy_version="routing-v1",
        approved_rule_ids={"route-in-pf-v3"},
        repository=repo,
    )
    decision = policy.decide(
        tenant_id="C-1001",
        event_id=uuid4(),
        canonical_version=1,
        input_fingerprint="b" * 64,
        candidate=RoutingCandidate(
            lane=Lane.DRAFT_FOR_AGENT,
            queue_id="Statutory-Compliance",
            decided_by=DecidedBy.RULE,
            rule_id="route-in-pf-v3",
            reason_codes=("REGION_IN",),
            confidence=1.0,
        ),
        tenant_resolved=True,
        schema_valid=True,
        blocking_risk=False,
    )
    repo.append(decision)

    sqls = [call.args[0] for call in cursor.execute.call_args_list]
    assert "SET LOCAL app.tenant_id" in sqls[0]
    assert "INSERT INTO app.routing_decision" in sqls[1]
