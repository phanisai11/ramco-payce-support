import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from payce_support.intake import (
    IdempotencyConflictError,
    ImmutableIntakeService,
    InMemoryIntakeRepository,
)
from payce_support.models import (
    AccessContext,
    AttachmentInput,
    AuthenticatedIngressContext,
    DecidedBy,
    IngestCommand,
    Lane,
    Region,
    Source,
    TenantRecord,
    TenantResolutionStatus,
)
from payce_support.routing import (
    InMemoryRoutingDecisionRepository,
    RoutingCandidate,
    RoutingPolicy,
)
from payce_support.storage import (
    InMemoryImmutableObjectStore,
    InMemoryObjectManifestRepository,
    ObjectAlreadyExistsError,
    StorageBroker,
    TenantAccessDeniedError,
)
from payce_support.tenancy import TenantResolver


@pytest.fixture
def tenants() -> tuple[TenantRecord, TenantRecord]:
    return (
        TenantRecord(
            tenant_id="C-1001",
            tenant_uuid=UUID("00000000-0000-0000-0000-000000001001"),
            region=Region.INDIA,
        ),
        TenantRecord(
            tenant_id="C-1002",
            tenant_uuid=UUID("00000000-0000-0000-0000-000000001002"),
            region=Region.INDIA,
        ),
    )


@pytest.fixture
def resolver(tenants: tuple[TenantRecord, TenantRecord]) -> TenantResolver:
    return TenantResolver(
        tenants,
        verified_contacts={
            "contact-a": {"C-1001"},
            "conflicted": {"C-1001", "C-1002"},
        },
        mapping_version="contacts-v1",
    )


@pytest.fixture
def intake_components(
    resolver: TenantResolver,
) -> tuple[
    ImmutableIntakeService,
    InMemoryImmutableObjectStore,
    InMemoryObjectManifestRepository,
]:
    objects = InMemoryImmutableObjectStore()
    manifests = InMemoryObjectManifestRepository()
    service = ImmutableIntakeService(
        tenant_resolver=resolver,
        object_store=objects,
        manifests=manifests,
        intake_repository=InMemoryIntakeRepository(),
    )
    return service, objects, manifests


def _context(tenant_id: str | None = "C-1001") -> AuthenticatedIngressContext:
    return AuthenticatedIngressContext(
        principal_id="principal-1",
        channel_account_id="widget-in",
        ingress_region=Region.INDIA,
        authenticated_tenant_id=tenant_id,
    )


def _command(
    raw_body: bytes = b'{"message":"help"}',
    attachment_body: bytes = b"%PDF-synthetic",
) -> IngestCommand:
    return IngestCommand(
        source=Source.WIDGET,
        source_event_id="widget-evt-1",
        received_at=datetime(2026, 11, 6, 9, tzinfo=timezone.utc),
        source_occurred_at=datetime(2026, 11, 6, 8, 59, tzinfo=timezone.utc),
        payload_schema="widget/v1",
        raw_body=raw_body,
        parsed_payload={"message": "help", "tenant_id": "C-1002"},
        attachments=(
            AttachmentInput(
                original_filename="../../other-tenant/payslip.pdf",
                claimed_media_type="application/pdf",
                body=attachment_body,
            ),
        ),
    )


def test_tenant_is_derived_from_authenticated_context_not_payload(
    intake_components: tuple[
        ImmutableIntakeService,
        InMemoryImmutableObjectStore,
        InMemoryObjectManifestRepository,
    ],
) -> None:
    service, _, manifests = intake_components

    result = service.ingest(context=_context("C-1001"), command=_command())

    resolution = result.envelope.tenant_resolution
    assert resolution.status is TenantResolutionStatus.RESOLVED
    assert resolution.tenant is not None
    assert resolution.tenant.tenant_id == "C-1001"
    raw_manifest = manifests.get(result.envelope.raw_body_ref.object_id)
    assert "C-1002" not in raw_manifest.object_path


def test_intake_preserves_exact_bytes_and_never_uses_filename_as_path(
    intake_components: tuple[
        ImmutableIntakeService,
        InMemoryImmutableObjectStore,
        InMemoryObjectManifestRepository,
    ],
) -> None:
    service, objects, manifests = intake_components
    command = _command(b'{ "spacing" : "is evidence" }\n')

    result = service.ingest(context=_context(), command=command)

    raw_manifest = manifests.get(result.envelope.raw_body_ref.object_id)
    assert objects.read_internal(raw_manifest.object_path) == command.raw_body
    receipt = result.envelope.attachment_receipts[0]
    manifest_path = manifests.get(receipt.object_id).object_path
    assert ".." not in manifest_path
    assert "payslip.pdf" not in manifest_path


def test_retry_is_idempotent_and_changed_bytes_conflict(
    intake_components: tuple[
        ImmutableIntakeService,
        InMemoryImmutableObjectStore,
        InMemoryObjectManifestRepository,
    ],
) -> None:
    service, _, _ = intake_components

    first = service.ingest(context=_context(), command=_command())
    retry = service.ingest(context=_context(), command=_command())

    assert retry.envelope.event_id == first.envelope.event_id
    assert retry.envelope_ref.generation == first.envelope_ref.generation

    with pytest.raises(IdempotencyConflictError):
        service.ingest(context=_context(), command=_command(b'{"changed":true}'))

    with pytest.raises(IdempotencyConflictError):
        service.ingest(
            context=_context(),
            command=_command(attachment_body=b"%PDF-changed"),
        )


def test_immutable_store_rejects_overwrite() -> None:
    objects = InMemoryImmutableObjectStore()
    object_id = uuid4()
    objects.create(object_id=object_id, object_path="in/path", body=b"one")

    with pytest.raises(ObjectAlreadyExistsError):
        objects.create(object_id=object_id, object_path="in/path", body=b"two")


def test_storage_broker_denies_cross_tenant_read(
    intake_components: tuple[
        ImmutableIntakeService,
        InMemoryImmutableObjectStore,
        InMemoryObjectManifestRepository,
    ],
) -> None:
    service, objects, manifests = intake_components
    result = service.ingest(context=_context("C-1001"), command=_command())
    broker = StorageBroker(objects, manifests)
    object_id = result.envelope.attachment_receipts[0].object_id

    allowed = broker.read(
        context=AccessContext(
            principal_id="agent-a",
            tenant_id="C-1001",
            region=Region.INDIA,
        ),
        object_id=object_id,
        purpose="support-investigation",
    )
    assert allowed == b"%PDF-synthetic"

    with pytest.raises(TenantAccessDeniedError):
        broker.read(
            context=AccessContext(
                principal_id="agent-b",
                tenant_id="C-1002",
                region=Region.INDIA,
            ),
            object_id=object_id,
            purpose="support-investigation",
        )


def test_unresolved_identity_is_quarantined_and_not_broker_readable(
    intake_components: tuple[
        ImmutableIntakeService,
        InMemoryImmutableObjectStore,
        InMemoryObjectManifestRepository,
    ],
) -> None:
    service, objects, manifests = intake_components
    result = service.ingest(context=_context(None), command=_command())
    assert result.envelope.tenant_resolution.status is TenantResolutionStatus.UNRESOLVED
    raw_manifest = manifests.get(result.envelope.raw_body_ref.object_id)
    assert "/unresolved/" in raw_manifest.object_path

    broker = StorageBroker(objects, manifests)
    with pytest.raises(TenantAccessDeniedError):
        broker.read(
            context=AccessContext(
                principal_id="agent-a",
                tenant_id="C-1001",
                region=Region.INDIA,
            ),
            object_id=result.envelope.raw_body_ref.object_id,
            purpose="support-investigation",
        )


def test_conflicting_contact_mapping_fails_closed(resolver: TenantResolver) -> None:
    resolution = resolver.resolve(
        AuthenticatedIngressContext(
            principal_id="contact",
            channel_account_id="email-in",
            ingress_region=Region.INDIA,
            verified_contact_id="conflicted",
        ),
    )
    assert resolution.status is TenantResolutionStatus.CONFLICT
    assert resolution.tenant is None


def test_payload_contract_rejects_naive_timestamp_and_unknown_field() -> None:
    values = {
        "source": Source.EMAIL,
        "source_event_id": "email-1",
        "received_at": datetime(2026, 11, 6, 8, 0),
        "payload_schema": "email/v1",
        "raw_body": b"body",
        "parsed_payload": {},
        "unknown": "rejected",
    }
    with pytest.raises(ValidationError):
        IngestCommand.model_validate(values)


def test_serialized_raw_envelope_matches_published_schema(
    intake_components: tuple[
        ImmutableIntakeService,
        InMemoryImmutableObjectStore,
        InMemoryObjectManifestRepository,
    ],
) -> None:
    service, _, _ = intake_components
    result = service.ingest(context=_context(), command=_command())
    schema = json.loads(Path("schemas/raw-intake-event.schema.json").read_text())
    payload = json.loads(result.envelope.model_dump_json(by_alias=True))

    Draft202012Validator(schema).validate(payload)


def test_only_g3_approved_rule_can_auto_route() -> None:
    repository = InMemoryRoutingDecisionRepository()
    policy = RoutingPolicy(
        policy_version="routing-v1",
        approved_rule_ids={"route-in-pf-v3"},
        repository=repository,
    )
    fingerprint = sha256(b"canonical-event").hexdigest()
    candidate = RoutingCandidate(
        lane=Lane.DRAFT_FOR_AGENT,
        queue_id="Statutory-Compliance",
        decided_by=DecidedBy.RULE,
        rule_id="route-in-pf-v3",
        reason_codes=("REGION_IN", "ENTITY_PF"),
        confidence=1.0,
    )

    decision = policy.decide(
        tenant_id="C-1001",
        event_id=uuid4(),
        canonical_version=1,
        input_fingerprint=fingerprint,
        candidate=candidate,
        tenant_resolved=True,
        schema_valid=True,
        blocking_risk=False,
    )

    assert decision.auto_route is True
    assert decision.queue_id == "Statutory-Compliance"
    schema = json.loads(Path("schemas/routing-decision.schema.json").read_text())
    payload = json.loads(decision.model_dump_json(by_alias=True))
    Draft202012Validator(schema).validate(payload)


@pytest.mark.parametrize("decided_by", [DecidedBy.LLM, DecidedBy.SUGGEST])
def test_llm_and_suggestion_always_abstain(decided_by: DecidedBy) -> None:
    policy = RoutingPolicy(
        policy_version="routing-v1",
        approved_rule_ids=set(),
        repository=InMemoryRoutingDecisionRepository(),
    )
    candidate = RoutingCandidate(
        lane=Lane.DRAFT_FOR_AGENT,
        queue_id="General-L1",
        decided_by=decided_by,
        reason_codes=("AMBIGUOUS_TEXT",),
        confidence=0.99,
    )

    decision = policy.decide(
        tenant_id="C-1001",
        event_id=uuid4(),
        canonical_version=1,
        input_fingerprint=sha256(b"event").hexdigest(),
        candidate=candidate,
        tenant_resolved=True,
        schema_valid=True,
        blocking_risk=False,
    )

    assert decision.auto_route is False
    assert decision.queue_id is None
    assert decision.lane is Lane.HUMAN
    assert "ABSTAIN_NON_AUTHORITATIVE_DECIDER" in decision.reason_codes


def test_migration_contains_tenant_and_append_only_controls() -> None:
    sql = Path("migrations/001_tenant_foundation.sql").read_text()
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "FOREIGN KEY (tenant_id, event_id)" in sql
    assert "reject_append_only_mutation" in sql
    assert "BYPASSRLS" not in sql


def test_migration_002_storage_objects_are_rls_bound_without_public_lookup() -> None:
    sql = Path("migrations/002_storage_object_and_idempotency.sql").read_text()
    assert "app.storage_object" in sql
    assert "intake_quarantine.intake_idempotency" in sql
    assert "UNIQUE (object_id)" in sql
    assert "storage_object_isolation" in sql
    assert "SECURITY DEFINER" not in sql
    assert "GRANT EXECUTE" not in sql


def test_migration_003_shared_kb_has_effective_time_and_vector_indexes() -> None:
    sql = Path("migrations/003_shared_kb_vector.sql").read_text()
    assert "app.shared_kb_chunk" in sql
    assert "valid_to > valid_from" in sql
    assert "embedding_profile" in sql
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
    assert "shared_kb_chunk_append_only" in sql
