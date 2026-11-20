"""Canonical ticket event contract (com.payce.ticket.canonical.v1).

Mirrors ``schemas/canonical-ticket-event.schema.json``. Canonical records are
immutable versions; corrections create ``canonical_version + 1`` and never edit
v1 (SAAS_TENANT_DATA_CONTRACT.md §5).

``CanonicalExtraction`` is the output of the deterministic L1/DLP stage: it
supplies ticket/case identity, already-masked content, facts, and per-attachment
scan/PII assessments. The normalizer never invents tenant identity — it is taken
from the resolved tenant on the raw intake envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AliasChoices, Field, field_validator, model_validator

from payce_support.models import (
    AttachmentReceipt,
    ImmutableModel,
    PiiClass,
    ProcessingStatus,
    RawIntakeEnvelope,
    Region,
    ScanStatus,
    TenantResolutionMethod,
    TenantResolutionStatus,
)

NORMALIZER_VERSION = "canonical-normalizer-2026-11-13.1"
DLP_POLICY_VERSION = "dlp-policy-2026-11-13.1"

CanonicalLanguage = Literal["en", "hi", "hi-Latn", "ar"]

_JIRA_KEY = r"^PNJ-[0-9]+$"
_KB_ID = r"^KB-[0-9]+$"
_SHA256 = r"^[a-f0-9]{64}$"
_GENERATION = r"^[0-9]+$"
_PAYROLL_PERIOD = r"^[0-9]{4}-(0[1-9]|1[0-2])$"


def _require_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return value


class CanonicalRawRef(ImmutableModel):
    object_id: UUID
    sha256: str = Field(pattern=_SHA256)
    generation: str = Field(pattern=_GENERATION)


class CanonicalTicket(ImmutableModel):
    ticket_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(pattern=r"^C-[0-9]{4,}$")
    region: Region


class CanonicalTenantResolution(ImmutableModel):
    status: Literal["resolved"]
    method: TenantResolutionMethod
    mapping_version: str = Field(min_length=1, max_length=64)


class CanonicalTime(ImmutableModel):
    received_at: datetime
    source_occurred_at: datetime | None
    customer_message_at: datetime
    payroll_period: str | None = Field(default=None, pattern=_PAYROLL_PERIOD)
    t_as_of: datetime
    payroll_week: bool

    @model_validator(mode="after")
    def timestamps_are_aware(self) -> CanonicalTime:
        _require_aware(self.received_at, "received_at")
        if self.source_occurred_at is not None:
            _require_aware(self.source_occurred_at, "source_occurred_at")
        _require_aware(self.customer_message_at, "customer_message_at")
        _require_aware(self.t_as_of, "t_as_of")
        return self


class CanonicalContent(ImmutableModel):
    subject_masked: str = Field(max_length=1000)
    body_masked: str = Field(max_length=100000)
    language: CanonicalLanguage
    content_sha256: str = Field(pattern=_SHA256)


class CanonicalFacts(ImmutableModel):
    run_id: str | None = Field(default=None, max_length=128)
    jira_keys: tuple[str, ...] = Field(default=(), max_length=50)
    kb_ids: tuple[str, ...] = Field(default=(), max_length=50)
    error_codes: tuple[str, ...] = Field(default=(), max_length=50)
    refund_refs: tuple[str, ...] = Field(default=(), max_length=20)
    category: str | None = Field(default=None, max_length=64)

    @field_validator("jira_keys")
    @classmethod
    def jira_keys_match(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not item.startswith("PNJ-") or not item[4:].isdigit():
                raise ValueError("jira_keys must match PNJ-<digits>")
        return value

    @field_validator("kb_ids")
    @classmethod
    def kb_ids_match(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not item.startswith("KB-") or not item[3:].isdigit():
                raise ValueError("kb_ids must match KB-<digits>")
        return value

    @field_validator("error_codes")
    @classmethod
    def error_codes_bounded(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not 1 <= len(item) <= 64:
                raise ValueError("error_codes entries must be 1-64 chars")
        return value

    @field_validator("refund_refs")
    @classmethod
    def refund_refs_bounded(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not 1 <= len(item) <= 64:
                raise ValueError("refund_refs entries must be 1-64 chars")
        return value


class CanonicalAttachment(ImmutableModel):
    attachment_id: UUID
    manifest_version: int = Field(ge=1)
    detected_media_type: str = Field(min_length=1, max_length=127)
    size_bytes: int = Field(ge=0, le=52_428_800)
    sha256: str = Field(pattern=_SHA256)
    scan_status: ScanStatus
    pii_class: PiiClass
    processing_status: ProcessingStatus


class CanonicalProvenance(ImmutableModel):
    normalizer_version: str = Field(min_length=1, max_length=64)
    dlp_policy_version: str = Field(min_length=1, max_length=64)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "created_at")


class CanonicalTicketEvent(ImmutableModel):
    schema_name: str = Field(
        default="com.payce.ticket.canonical.v1",
        serialization_alias="schema",
        validation_alias=AliasChoices("schema", "schema_name"),
    )
    event_id: UUID
    canonical_version: int = Field(ge=1)
    raw_ref: CanonicalRawRef
    ticket: CanonicalTicket
    tenant_resolution: CanonicalTenantResolution
    time: CanonicalTime
    content: CanonicalContent
    facts: CanonicalFacts
    attachments: tuple[CanonicalAttachment, ...] = Field(max_length=20)
    provenance: CanonicalProvenance


class CanonicalAttachmentInput(ImmutableModel):
    """Per-attachment DLP/scan assessment supplied by the L1 stage."""

    detected_media_type: str | None = Field(default=None, max_length=127)
    manifest_version: int = Field(default=1, ge=1)
    scan_status: ScanStatus = ScanStatus.PENDING
    pii_class: PiiClass = PiiClass.NONE
    processing_status: ProcessingStatus = ProcessingStatus.METADATA_ONLY


class CanonicalExtraction(ImmutableModel):
    """Deterministic L1 output consumed by the canonical normalizer."""

    ticket_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    subject_masked: str = Field(default="", max_length=1000)
    body_masked: str = Field(default="", max_length=100000)
    language: CanonicalLanguage = "en"
    customer_message_at: datetime | None = None
    payroll_period: str | None = Field(default=None, pattern=_PAYROLL_PERIOD)
    payroll_week: bool = False
    t_as_of: datetime | None = None
    facts: CanonicalFacts = Field(default_factory=CanonicalFacts)
    attachment_assessments: dict[UUID, CanonicalAttachmentInput] = Field(
        default_factory=dict,
    )

    @field_validator("customer_message_at", "t_as_of")
    @classmethod
    def optional_timestamps_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            _require_aware(value, "timestamp")
        return value


def content_fingerprint(*, tenant_id: str, raw_sha256: str, canonical_version: int) -> str:
    """Pipeline fingerprint from PRODUCTION_DESIGN_GCP §4.

    ``SHA256(tenant_id + raw_event_sha256 + canonical_version)`` with ``:``
    separators so field boundaries cannot collide.
    """
    return sha256(
        f"{tenant_id}:{raw_sha256}:{canonical_version}".encode("utf-8"),
    ).hexdigest()


def content_sha256(*, subject_masked: str, body_masked: str) -> str:
    """Hash of masked canonical content (unit-separator delimited)."""
    return sha256(f"{subject_masked}\x1f{body_masked}".encode("utf-8")).hexdigest()


def build_canonical_event(
    *,
    envelope: RawIntakeEnvelope,
    extraction: CanonicalExtraction,
    canonical_version: int = 1,
    created_at: datetime | None = None,
) -> CanonicalTicketEvent:
    """Assemble a canonical v1 event from a resolved raw intake envelope.

    Raises ``ValueError`` when the envelope tenant is unresolved/conflicting —
    canonical events are tenant-bound by contract.
    """
    resolution = envelope.tenant_resolution
    if resolution.status is not TenantResolutionStatus.RESOLVED or resolution.tenant is None:
        raise ValueError("canonical events require a resolved tenant")
    if resolution.method is None:
        raise ValueError("resolved tenant requires a resolution method")

    tenant = resolution.tenant
    customer_message_at = extraction.customer_message_at or envelope.received_at
    t_as_of = extraction.t_as_of or customer_message_at

    attachments = tuple(
        _canonical_attachment(receipt=receipt, assessment=extraction.attachment_assessments.get(
            receipt.attachment_id,
        ))
        for receipt in envelope.attachment_receipts
    )

    return CanonicalTicketEvent(
        event_id=envelope.event_id,
        canonical_version=canonical_version,
        raw_ref=CanonicalRawRef(
            object_id=envelope.raw_body_ref.object_id,
            sha256=envelope.raw_body_ref.sha256,
            generation=envelope.raw_body_ref.generation,
        ),
        ticket=CanonicalTicket(
            ticket_id=extraction.ticket_id,
            case_id=extraction.case_id,
            tenant_id=tenant.tenant_id,
            region=tenant.region,
        ),
        tenant_resolution=CanonicalTenantResolution(
            status="resolved",
            method=resolution.method,
            mapping_version=resolution.mapping_version,
        ),
        time=CanonicalTime(
            received_at=envelope.received_at,
            source_occurred_at=envelope.source_occurred_at,
            customer_message_at=customer_message_at,
            payroll_period=extraction.payroll_period,
            t_as_of=t_as_of,
            payroll_week=extraction.payroll_week,
        ),
        content=CanonicalContent(
            subject_masked=extraction.subject_masked,
            body_masked=extraction.body_masked,
            language=extraction.language,
            content_sha256=content_sha256(
                subject_masked=extraction.subject_masked,
                body_masked=extraction.body_masked,
            ),
        ),
        facts=extraction.facts,
        attachments=attachments,
        provenance=CanonicalProvenance(
            normalizer_version=NORMALIZER_VERSION,
            dlp_policy_version=DLP_POLICY_VERSION,
            created_at=created_at or datetime.now(timezone.utc),
        ),
    )


def _canonical_attachment(
    *,
    receipt: AttachmentReceipt,
    assessment: CanonicalAttachmentInput | None,
) -> CanonicalAttachment:
    assessment = assessment or CanonicalAttachmentInput()
    detected = assessment.detected_media_type or receipt.claimed_media_type
    if detected is None:
        detected = "application/octet-stream"
    return CanonicalAttachment(
        attachment_id=receipt.attachment_id,
        manifest_version=assessment.manifest_version,
        detected_media_type=detected,
        size_bytes=receipt.size_bytes,
        sha256=receipt.sha256,
        scan_status=assessment.scan_status,
        pii_class=assessment.pii_class,
        processing_status=assessment.processing_status,
    )


class CanonicalEventRepository(Protocol):
    def append(self, event: CanonicalTicketEvent) -> None: ...

    def get(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        canonical_version: int,
    ) -> CanonicalTicketEvent | None: ...

    def latest_version(self, *, tenant_id: str, event_id: UUID) -> int:
        """Highest stored canonical_version for the event; 0 when absent."""
        ...


class InMemoryCanonicalEventRepository:
    def __init__(self) -> None:
        self._events: dict[tuple[str, UUID, int], CanonicalTicketEvent] = {}

    def append(self, event: CanonicalTicketEvent) -> None:
        key = (event.ticket.tenant_id, event.event_id, event.canonical_version)
        if key in self._events:
            raise ValueError("canonical events are append-only")
        self._events[key] = event

    def get(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        canonical_version: int,
    ) -> CanonicalTicketEvent | None:
        return self._events.get((tenant_id, event_id, canonical_version))

    def latest_version(self, *, tenant_id: str, event_id: UUID) -> int:
        versions = [
            version
            for (tid, eid, version) in self._events
            if tid == tenant_id and eid == event_id
        ]
        return max(versions, default=0)
