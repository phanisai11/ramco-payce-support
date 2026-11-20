"""HTTP request/response models (API boundary)."""

from __future__ import annotations

import base64
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from payce_support.models import (
    AttachmentInput,
    AuthenticatedIngressContext,
    IngestCommand,
    Region,
    Source,
)
from payce_support.statutory_engine import StatutoryResolution


class AttachmentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_filename: str = Field(min_length=1, max_length=255)
    claimed_media_type: str | None = Field(default=None, max_length=127)
    body_base64: str = Field(description="Base64-encoded attachment bytes")

    def to_domain(self) -> AttachmentInput:
        raw = base64.b64decode(self.body_base64, validate=True)
        return AttachmentInput(
            original_filename=self.original_filename,
            claimed_media_type=self.claimed_media_type,
            body=raw,
        )


class IngestHttpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str = Field(min_length=1, max_length=256)
    channel_account_id: str = Field(min_length=1, max_length=256)
    ingress_region: Region
    authenticated_tenant_id: str | None = Field(default=None, pattern=r"^C-[0-9]{4,}$")
    verified_contact_id: str | None = Field(default=None, max_length=256)

    source: Source
    source_event_id: str = Field(min_length=1, max_length=256)
    received_at: datetime
    source_occurred_at: datetime | None = None
    payload_schema: str = Field(min_length=1, max_length=128)
    raw_body_base64: str
    parsed_payload: dict[str, Any]
    attachments: tuple[AttachmentPart, ...] = Field(default_factory=tuple)

    def to_context(self) -> AuthenticatedIngressContext:
        return AuthenticatedIngressContext(
            principal_id=self.principal_id,
            channel_account_id=self.channel_account_id,
            ingress_region=self.ingress_region,
            authenticated_tenant_id=self.authenticated_tenant_id,
            verified_contact_id=self.verified_contact_id,
        )

    def to_command(self) -> IngestCommand:
        raw_body = base64.b64decode(self.raw_body_base64, validate=True)
        atts = tuple(p.to_domain() for p in self.attachments)
        return IngestCommand(
            source=self.source,
            source_event_id=self.source_event_id,
            received_at=self.received_at,
            source_occurred_at=self.source_occurred_at,
            payload_schema=self.payload_schema,
            raw_body=raw_body,
            parsed_payload=self.parsed_payload,
            attachments=atts,
        )


class IngestHttpResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    envelope_json: dict[str, Any]


class StatutoryResolveQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str
    topic: str
    as_of: date


class StatutoryResolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    region: str
    topic: str
    effective_from: date
    value_applied: str
    source: str

    @classmethod
    def from_resolution(cls, r: StatutoryResolution) -> StatutoryResolveResponse:
        return cls(
            rule_id=r.rule_id,
            region=r.region,
            topic=r.topic,
            effective_from=r.effective_from,
            value_applied=r.value_applied,
            source=r.source,
        )


class RoutingClassifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(pattern=r"^C-[0-9]{4,}$")
    text: str = Field(min_length=1, max_length=20_000)
    region: str = Field(description="Region code: IN or UAE", pattern=r"^(IN|UAE)$")
    p1: bool = False
    assist: bool | None = Field(
        default=None,
        description="Request LLM signal extraction; defaults to PAYCE_ROUTING_LLM",
    )


class RoutingPolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    queue_id: str
    lane: str
    region: str | None = None
    tenant_id: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    pattern: str
    signals: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    status: str = "approved"


class RoutingPolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: str
    approved_rule_ids: tuple[str, ...]
    rules: tuple[RoutingPolicyRule, ...]
    tenant_contexts: dict[str, dict[str, Any]]
    ticket_statuses: tuple[str, ...] = ()


class RoutingAssistInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    used: bool
    p1: bool | None = None
    priority: str | None = None
    entities: tuple[str, ...] = ()
    intent: str | None = None
    confidence: float | None = None
    model_id: str | None = None
    degraded_reason: str | None = None
    summary: str | None = None
    rag_hints: tuple[str, ...] = ()


class RoutingClassifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_route: bool
    queue_id: str | None
    lane: str
    rule_id: str | None
    reason_codes: tuple[str, ...]
    decided_by: str
    assist: RoutingAssistInfo | None = None


class TriageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(pattern=r"^C-[0-9]{4,}$")
    text: str = Field(min_length=1, max_length=20_000)
    region: str = Field(description="Region code: IN or UAE", pattern=r"^(IN|UAE)$")
    p1: bool = False


class TriageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str | None
    reply: str | None
    missing: tuple[str, ...] = ()
    decision: dict[str, Any] | None = None
    candidate: dict[str, Any] | None = None
    analysis_status: str | None = None
    signals: dict[str, Any] | None = None


class IncidentSignalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(pattern=r"^C-[0-9]{4,}$")
    channel: str = Field(min_length=1, max_length=64)
    received_at: datetime
    error_code: str | None = None
    subject: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1, max_length=20_000)


class IncidentSignalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    declared: bool
    symptom_fingerprint: str | None = None
    fifth_ticket_at: datetime | None = None
    declared_at: datetime | None = None


class PipelineProcessRequest(IngestHttpRequest):
    """Ingest payload plus pipeline controls. Ingest stays idempotent; each
    call to ``/v1/pipeline/process`` records a new canonical version
    (reprocessing semantics per SAAS_TENANT_DATA_CONTRACT.md §5)."""

    p1: bool = False
    priority: str | None = Field(
        default=None,
        pattern=r"^p[1-4]$",
        description="Declared ticket priority P1–P4; P1 also forces the "
        "p1 routing flag",
    )
    ticket_status: str = Field(default="open", max_length=64)
    ticket_age_hours: float | None = Field(
        default=None,
        ge=0,
        le=24 * 365,
        description="Demo/simulation: backdate ticket_current.updated_at",
    )


class PipelineProcessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    canonical_version: int
    ticket_id: str
    case_id: str
    auto_route: bool
    lane: str
    queue_id: str | None
    rule_id: str | None
    reason_codes: tuple[str, ...]
    decided_by: str
    attachment_count: int = 0
    incident_declared: bool = False


class PubSubPushMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: str = Field(description="Base64-encoded Pub/Sub message data")
    attributes: dict[str, str] = Field(default_factory=dict)
    message_id: str | None = None


class PubSubPushRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: PubSubPushMessage
    subscription: str | None = None


class PubSubPushResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    event_id: str | None = None
    canonical_version: int | None = None
    reason: str | None = None


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"


class RoutingRuleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(
        default=None, pattern=r"^(approved|in_progress|outdated|draft|expired)$"
    )
    tenant_id: str | None = Field(default=None, pattern=r"^C-[0-9]{4,}$")
    region: str | None = Field(default=None, pattern=r"^(IN|UAE)$")
    queue_id: str | None = Field(default=None, max_length=128)
    lane: str | None = Field(default=None, max_length=64)
    valid_from: str | None = None
    valid_to: str | None = None


class TenantContextUpsertRequest(BaseModel):
    """Full tenant-context document (LLM guidance, not SLA policy)."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=128)
    region: str = Field(pattern=r"^(IN|UAE)$")
    queue_hints: dict[str, str] = {}
    priority_guidance: tuple[str, ...] = ()
    kb_hints: tuple[dict[str, Any], ...] = ()
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("priority_guidance", "kb_hints", mode="before")
    @classmethod
    def _lists_to_tuples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class SlaRuleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(
        default=None, pattern=r"^(approved|in_progress|outdated|draft|expired)$"
    )
    tenant_id: str | None = Field(default=None, pattern=r"^C-[0-9]{4,}$")
    region: str | None = Field(default=None, pattern=r"^(IN|UAE)$")
    unanswered_hours: float | None = Field(default=None, gt=0)
    penalty_amount: str | None = Field(default=None, max_length=32)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    valid_from: str | None = None
    valid_to: str | None = None
    description: str | None = Field(default=None, max_length=256)
    applies_to_statuses: tuple[str, ...] | None = Field(default=None, max_length=10)
    applies_to_priorities: tuple[str, ...] | None = Field(default=None, max_length=10)


class SlaRuleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = Field(default=None, pattern=r"^C-[0-9]{4,}$")
    region: str | None = Field(default=None, pattern=r"^(IN|UAE)$")
    unanswered_hours: float = Field(gt=0)
    penalty_amount: str = Field(min_length=1, max_length=32)
    currency: str = Field(min_length=3, max_length=8)
    valid_from: str | None = None
    valid_to: str | None = None
    status: str = Field(
        default="approved",
        pattern=r"^(approved|in_progress|outdated|draft|expired)$",
    )
    applies_to_statuses: tuple[str, ...] | None = Field(default=None, max_length=10)
    applies_to_priorities: tuple[str, ...] | None = Field(default=None, max_length=10)
    description: str | None = Field(default=None, max_length=256)


class KbArticleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=256)
    topic: str = Field(min_length=1, max_length=128)
    excerpt: str = Field(min_length=1, max_length=20000)
    region: str = Field(default="ALL", pattern=r"^(ALL|IN|UAE)$")
    valid_from: str | None = None
    valid_to: str | None = None
    years: tuple[int, ...] | None = Field(default=None, max_length=20)
    status: str = Field(default="Published", pattern=r"^(Published|Draft|Archived|published|draft|archived)$")
    owner: str | None = Field(default=None, max_length=128)
    last_reviewed: str | None = None


TICKET_STATUSES = ("open", "in_progress", "pending_customer", "resolved", "closed")


class TicketStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(pattern=r"^(open|in_progress|pending_customer|resolved|closed)$")
    queue_id: str | None = Field(default=None, max_length=128)


class DashboardTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    ticket_id: str
    status: str
    queue_id: str | None
    rule_id: str | None = None
    lane: str | None = None
    auto_route: bool | None = None
    decided_by: str | None = None
    canonical_version: int
    event_id: str
    updated_at: datetime
    human_intervened_at: datetime | None = None
    source: str | None = None
    priority: str | None = None
    dedup_of: str | None = None
    csat_score: int | None = None
    csat_at: datetime | None = None
    csat_comment: str | None = None


class DashboardTicketDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket: DashboardTicket
    canonical: dict[str, Any] | None
    decision: dict[str, Any] | None
    sla: dict[str, Any] | None = None
    replies: tuple[dict[str, Any], ...] = ()
    analysis: dict[str, Any] | None = None
    llm_answer: dict[str, Any] | None = None
    llm_queue: dict[str, Any] | None = None
    llm_cost: dict[str, Any] | None = None


class TicketReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    author: Literal["agent", "customer", "bot"]
    body: str = Field(min_length=1, max_length=20000)


class DashboardSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    by_status: dict[str, int]
    by_queue: dict[str, int]
    by_channel: dict[str, int] = {}
    auto_routed: int
    tickets: tuple[DashboardTicket, ...]


class DashboardAnalyticsResponse(BaseModel):
    """Home-screen analytics: KPIs, breakdowns, SLA risk, recent tickets."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    total: int
    open_count: int
    pending_agent_count: int
    resolved_count: int
    closed_count: int
    bot_solved_count: int
    human_solved_count: int
    automation_rate: float
    by_status: dict[str, int]
    by_queue: dict[str, int]
    by_channel: dict[str, int]
    channel_stats: tuple[dict[str, Any], ...] = ()
    by_day: tuple[dict[str, Any], ...]
    sla_breached_count: int
    sla_unanswered_count: int
    sla_attainment_pct: float | None = None
    sla_penalties_by_currency: dict[str, str]
    sla_by_tenant: tuple[dict[str, Any], ...] = ()
    sla_at_risk: tuple[dict[str, Any], ...]
    recent: tuple[DashboardTicket, ...]


class TicketDraftResponse(BaseModel):
    """Bot-draft evaluation for one ticket: verdict + grounded answer."""

    model_config = ConfigDict(extra="forbid")

    verdict: str  # auto_answer | draft_for_agent | human_only
    verdict_reasons: tuple[str, ...]
    rag_status: str  # answered | abstained
    rag_reason_code: str
    answer: str | None
    citations: tuple[str, ...]
    retrieved_chunk_ids: tuple[str, ...]
    kb_articles: tuple[dict[str, str], ...] = ()
    model_id: str | None
    cache_hit: bool
    input_tokens: int
    output_tokens: int
    llm_provider: str  # "openrouter" | "local_extractive"
