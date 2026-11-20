"""Strict immutable domain models for intake, tenancy, files, and routing."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class ImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Region(StrEnum):
    INDIA = "IN"
    UAE = "UAE"


class Source(StrEnum):
    EMAIL = "email"
    WIDGET = "widget"
    PARTNER = "partner"
    WHATSAPP = "whatsapp"
    PHONE = "phone"


class TenantResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    CONFLICT = "conflict"


class TenantResolutionMethod(StrEnum):
    AUTHENTICATED_SESSION = "authenticated_session"
    VERIFIED_CONTACT = "verified_contact"
    MANUAL = "manual"


class ScanStatus(StrEnum):
    CLEAN = "clean"
    BLOCKED = "blocked"
    PENDING = "pending"


class PiiClass(StrEnum):
    NONE = "none"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


class ProcessingStatus(StrEnum):
    METADATA_ONLY = "metadata_only"
    REDACTED_READY = "redacted_ready"
    HUMAN_ONLY = "human_only"


class Lane(StrEnum):
    BLOCK = "block"
    STATUS = "status"
    FILE_CHECK = "file_check"
    INCIDENT = "incident"
    AUTO_ANSWER = "auto_answer"
    DRAFT_FOR_AGENT = "draft_for_agent"
    HUMAN = "human"


class DecidedBy(StrEnum):
    RULE = "rule"
    POLICY_TABLE = "policy_table"
    ROUTING_SCORER = "routing_scorer"
    HUMAN = "human"
    SUGGEST = "suggest"
    LLM = "llm"


class TenantRecord(ImmutableModel):
    tenant_id: str = Field(pattern=r"^C-[0-9]{4,}$")
    tenant_uuid: UUID
    region: Region
    active: bool = True


class AuthenticatedIngressContext(ImmutableModel):
    principal_id: str = Field(min_length=1, max_length=256)
    channel_account_id: str = Field(min_length=1, max_length=256)
    ingress_region: Region
    authenticated_tenant_id: str | None = Field(
        default=None,
        pattern=r"^C-[0-9]{4,}$",
    )
    verified_contact_id: str | None = Field(default=None, max_length=256)


class TenantResolution(ImmutableModel):
    status: TenantResolutionStatus
    method: TenantResolutionMethod | None
    mapping_version: str
    tenant: TenantRecord | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def resolved_has_tenant(self) -> TenantResolution:
        if self.status is TenantResolutionStatus.RESOLVED:
            if self.tenant is None or self.method is None:
                raise ValueError("resolved tenant requires tenant and method")
        elif self.tenant is not None:
            raise ValueError("unresolved/conflicting identity cannot carry a tenant")
        return self


class AttachmentInput(ImmutableModel):
    original_filename: str = Field(min_length=1, max_length=255)
    claimed_media_type: str | None = Field(default=None, max_length=127)
    body: bytes = Field(max_length=52_428_800)


class IngestCommand(ImmutableModel):
    source: Source
    source_event_id: str = Field(min_length=1, max_length=256)
    received_at: datetime
    source_occurred_at: datetime | None = None
    payload_schema: str = Field(min_length=1, max_length=128)
    raw_body: bytes = Field(max_length=10_485_760)
    parsed_payload: dict[str, Any]
    attachments: tuple[AttachmentInput, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def timestamps_are_aware(self) -> IngestCommand:
        timestamps = (self.received_at, self.source_occurred_at)
        if any(value is not None and value.tzinfo is None for value in timestamps):
            raise ValueError("intake timestamps must include a timezone")
        received_offset = self.received_at.utcoffset()
        if received_offset is None or received_offset.total_seconds() != 0:
            raise ValueError("server received_at must be UTC")
        return self


class StoredObjectRef(ImmutableModel):
    object_id: UUID
    object_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    generation: str = Field(pattern=r"^[0-9]+$")
    size_bytes: int = Field(ge=0)


class RawObjectPointer(ImmutableModel):
    object_id: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    generation: str = Field(pattern=r"^[0-9]+$")
    size_bytes: int = Field(ge=0)


class AttachmentReceipt(ImmutableModel):
    attachment_id: UUID
    original_filename: str
    claimed_media_type: str | None
    size_bytes: int = Field(ge=0, le=52_428_800)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    object_generation: str = Field(pattern=r"^[0-9]+$")
    object_id: UUID


class RawIntakeEnvelope(ImmutableModel):
    schema_name: str = Field(
        default="com.payce.intake.raw.v1",
        serialization_alias="schema",
        validation_alias=AliasChoices("schema", "schema_name"),
    )
    event_id: UUID
    ingress_id: UUID
    source: Source
    source_event_id: str
    received_at: datetime
    source_occurred_at: datetime | None
    authenticated_subject: AuthenticatedIngressContext
    tenant_resolution: TenantResolution
    payload_schema: str
    raw_body_ref: RawObjectPointer
    payload: dict[str, Any]
    attachment_receipts: tuple[AttachmentReceipt, ...]


class ObjectManifest(ImmutableModel):
    object_id: UUID
    tenant_id: str | None
    region: Region
    object_path: str
    generation: str
    sha256: str
    size_bytes: int
    event_id: UUID
    attachment_id: UUID | None = None
    ingress_id: UUID | None = None


class AccessContext(ImmutableModel):
    principal_id: str = Field(min_length=1, max_length=256)
    tenant_id: str = Field(pattern=r"^C-[0-9]{4,}$")
    region: Region


class RoutingDecision(ImmutableModel):
    schema_name: str = Field(
        default="com.payce.routing.decision.v1",
        serialization_alias="schema",
        validation_alias=AliasChoices("schema", "schema_name"),
    )
    decision_id: UUID
    tenant_id: str = Field(pattern=r"^C-[0-9]{4,}$")
    event_id: UUID
    canonical_version: int = Field(ge=1)
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_version: str = Field(min_length=1, max_length=128)
    lane: Lane
    queue_id: str | None = Field(default=None, max_length=128)
    auto_route: bool
    decided_by: DecidedBy
    rule_id: str | None = Field(default=None, max_length=128)
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=50)
    confidence: float = Field(ge=0, le=1)
    supersedes_decision_id: UUID | None = None
    created_at: datetime

    @model_validator(mode="after")
    def enforce_auto_route_contract(self) -> RoutingDecision:
        approved_deciders = {
            DecidedBy.RULE,
            DecidedBy.POLICY_TABLE,
            DecidedBy.ROUTING_SCORER,
        }
        if self.auto_route:
            if self.decided_by not in approved_deciders:
                raise ValueError("automatic routing requires an approved deterministic decider")
            if not self.queue_id or not self.rule_id:
                raise ValueError("automatic routing requires queue_id and rule_id")
        if self.decided_by in {DecidedBy.LLM, DecidedBy.SUGGEST} and self.auto_route:
            raise ValueError("LLM/suggestion decisions can never auto-route")
        if self.created_at.tzinfo is None:
            raise ValueError("routing timestamp must include a timezone")
        return self
