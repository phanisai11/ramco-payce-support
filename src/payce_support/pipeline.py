"""Ticket pipeline store — append-only canonical/routing writes + current projection.

Sequence per SAAS_TENANT_DATA_CONTRACT.md §2/§6:

    raw_event_manifest + attachment_manifest (once per event)
    → canonical_ticket_event (versioned, append-only)
    → routing_decision (append-only, supersedes never edits)
    → ticket_current (mutable projection, rebuilt from events)

Unresolved/conflicting envelopes never reach this store: canonical events are
tenant-bound and ``build_canonical_event`` fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from payce_support.attachment_extract import AttachmentExtraction, extract_attachment
from payce_support.attachment_scan import scan_attachment
from payce_support.canonical import (
    CanonicalExtraction,
    CanonicalTicketEvent,
    build_canonical_event,
    content_fingerprint,
)
from payce_support.extraction import extract_canonical_envelope
from payce_support.intake import IntakeResult
from payce_support.models import (
    Lane,
    ObjectManifest,
    RawIntakeEnvelope,
    ScanStatus,
    TenantResolutionStatus,
)
from payce_support.routing import (
    InMemoryRoutingDecisionRepository,
    RoutingDecision,
    RoutingPolicy,
)
from payce_support.routing_matcher import routing_candidate_from_approved_rules
from payce_support.storage import (
    ImmutableObjectStore,
    ObjectManifestRepository,
)


@dataclass(frozen=True)
class TicketCurrentRow:
    """Mutable convenience projection — never the evidence record."""

    tenant_id: str
    ticket_id: str
    event_id: UUID
    canonical_version: int
    current_decision_id: UUID | None
    queue_id: str | None
    rule_id: str | None
    status: str
    updated_at: datetime
    human_intervened_at: datetime | None = None
    source: str | None = None
    contact_id: str | None = None
    symptom_fingerprint: str | None = None
    dedup_of: str | None = None
    text_fp: str | None = None
    csat_score: int | None = None
    csat_at: datetime | None = None
    csat_comment: str | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class TicketReply:
    """Append-only conversation entry (agent/customer/bot/system)."""

    reply_id: UUID
    author: str
    body: str
    created_at: datetime


@dataclass(frozen=True)
class TicketAnalysis:
    """Append-only saved analysis (LLM signals, draft evals, …)."""

    analysis_id: UUID
    kind: str
    canonical_version: int
    payload: dict
    model_id: str | None
    created_at: datetime


class TicketPipelineStore(Protocol):
    def record_ticket_event(
        self,
        *,
        envelope: RawIntakeEnvelope,
        raw_body_manifest: ObjectManifest,
        attachment_manifests: Sequence[ObjectManifest],
        extraction: CanonicalExtraction,
        decision: RoutingDecision,
        ticket_status: str = "open",
        updated_at: datetime | None = None,
    ) -> CanonicalTicketEvent:
        """Persist raw manifests, next canonical version, decision, projection."""
        ...

    def get_canonical(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        canonical_version: int,
    ) -> CanonicalTicketEvent | None: ...

    def latest_canonical_version(self, *, tenant_id: str, event_id: UUID) -> int: ...

    def get_ticket_current(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
    ) -> TicketCurrentRow | None: ...

    def get_decision(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        canonical_version: int,
    ) -> RoutingDecision | None:
        """Decision recorded for an event version; None when absent."""
        ...

    def list_ticket_current(
        self,
        *,
        tenant_id: str | None = None,
    ) -> tuple[TicketCurrentRow, ...]:
        """All ticket_current projections, optionally tenant-filtered."""
        ...

    def update_ticket_status(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        status: str,
        queue_id: str | None = None,
        touch_human: bool = False,
    ) -> TicketCurrentRow | None:
        """Mutable projection update (status/queue only — never evidence).

        ``touch_human`` stamps ``human_intervened_at`` for agent-driven
        changes (audit trail).
        """
        ...

    def close_stale_resolved(self, *, older_than_hours: float = 48.0) -> list[str]:
        """Auto-close tickets resolved longer than the threshold."""
        ...

    def add_reply(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        author: str,
        body: str,
    ) -> TicketReply | None:
        """Append a conversation reply; None when ticket unknown."""
        ...

    def list_replies(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
    ) -> tuple[TicketReply, ...]: ...

    def record_analysis(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        kind: str,
        canonical_version: int,
        payload: dict,
        model_id: str | None = None,
    ) -> TicketAnalysis | None:
        """Persist an analysis snapshot; None when ticket unknown."""
        ...

    def latest_analysis(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        kind: str | None = None,
    ) -> TicketAnalysis | None: ...

    def decisions_for_rows(self, rows) -> dict:
        """Batch current decisions: ``{(tenant_id, decision_id): decision}``."""
        ...

    def signal_priorities_for_rows(self, rows) -> dict:
        """Batch latest signal priorities: ``{(tenant_id, ticket_id): pri}``."""
        ...

    def record_attachment_extractions(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        extractions: dict[UUID, AttachmentExtraction],
    ) -> None:
        """Persist modality-processor output onto attachment manifests."""
        ...

    def list_attachments(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
    ) -> tuple[dict, ...]:
        """Attachment manifest rows (filename, media, scan, extraction)."""
        ...

    def mark_ticket_dedup(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        contact_id: str | None,
        symptom_fingerprint: str | None,
        dedup_of: str | None = None,
        text_fp: str | None = None,
    ) -> None:
        """Stamp dedup identity (contact + fingerprints) on
        ticket_current; ``dedup_of`` links a duplicate to its primary."""
        ...

    def find_open_duplicate(
        self,
        *,
        tenant_id: str,
        contact_id: str,
        symptom_fingerprint: str | None = None,
        text_fp: str | None = None,
        exclude_ticket_id: str,
        window_hours: float = 72.0,
    ) -> str | None:
        """Newest open primary ticket matching contact + either fingerprint."""
        ...


class InMemoryTicketPipelineStore:
    """Test/dev twin of the Postgres writer; same append-only semantics."""

    def __init__(self) -> None:
        self._raw_events: dict[tuple[str, UUID], ObjectManifest] = {}
        self._attachment_manifests: dict[
            tuple[str, UUID, int], ObjectManifest
        ] = {}
        self._canonical: dict[tuple[str, UUID, int], CanonicalTicketEvent] = {}
        self._decisions = InMemoryRoutingDecisionRepository()
        self._current: dict[tuple[str, str], TicketCurrentRow] = {}
        self._replies: dict[tuple[str, str], list[TicketReply]] = {}
        self._analyses: dict[tuple[str, str], list[TicketAnalysis]] = {}
        self._attachment_extractions: dict[
            tuple[str, UUID, UUID], AttachmentExtraction
        ] = {}

    def record_ticket_event(
        self,
        *,
        envelope: RawIntakeEnvelope,
        raw_body_manifest: ObjectManifest,
        attachment_manifests: Sequence[ObjectManifest],
        extraction: CanonicalExtraction,
        decision: RoutingDecision,
        ticket_status: str = "open",
        updated_at: datetime | None = None,
    ) -> CanonicalTicketEvent:
        tenant_id = decision.tenant_id
        event_id = envelope.event_id

        # Canonical version is allocated before the event is built so the
        # decision's canonical_version always matches the stored row.
        canonical_version = self.latest_canonical_version(
            tenant_id=tenant_id,
            event_id=event_id,
        ) + 1
        if decision.canonical_version != canonical_version:
            raise ValueError(
                "decision.canonical_version does not match next canonical version",
            )
        if decision.event_id != event_id:
            raise ValueError("decision.event_id does not match envelope event_id")

        event = build_canonical_event(
            envelope=envelope,
            extraction=extraction,
            canonical_version=canonical_version,
        )

        self._record_raw_manifests(
            envelope=envelope,
            raw_body_manifest=raw_body_manifest,
            attachment_manifests=attachment_manifests,
            canonical=event,
        )
        key = (tenant_id, event_id, canonical_version)
        if key in self._canonical:
            raise ValueError("canonical events are append-only")
        self._canonical[key] = event

        self._decisions.append(decision)
        self._current[(tenant_id, event.ticket.ticket_id)] = TicketCurrentRow(
            tenant_id=tenant_id,
            ticket_id=event.ticket.ticket_id,
            event_id=event_id,
            canonical_version=canonical_version,
            current_decision_id=decision.decision_id,
            queue_id=decision.queue_id,
            rule_id=decision.rule_id,
            status=ticket_status,
            updated_at=updated_at or datetime.now(timezone.utc),
            human_intervened_at=None,
            source=envelope.source.value,
        )
        return event

    def get_canonical(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        canonical_version: int,
    ) -> CanonicalTicketEvent | None:
        return self._canonical.get((tenant_id, event_id, canonical_version))

    def latest_canonical_version(self, *, tenant_id: str, event_id: UUID) -> int:
        versions = [
            version
            for (tid, eid, version) in self._canonical
            if tid == tenant_id and eid == event_id
        ]
        return max(versions, default=0)

    def get_ticket_current(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
    ) -> TicketCurrentRow | None:
        return self._current.get((tenant_id, ticket_id))

    def get_decision(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        canonical_version: int,
    ) -> RoutingDecision | None:
        return self._decisions.get_for_event(
            tenant_id=tenant_id,
            event_id=event_id,
            canonical_version=canonical_version,
        )

    def list_ticket_current(
        self,
        *,
        tenant_id: str | None = None,
    ) -> tuple[TicketCurrentRow, ...]:
        rows = [
            row
            for (tid, _ticket_id), row in self._current.items()
            if tenant_id is None or tid == tenant_id
        ]
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        return tuple(rows)

    def update_ticket_status(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        status: str,
        queue_id: str | None = None,
        touch_human: bool = False,
    ) -> TicketCurrentRow | None:
        row = self._current.get((tenant_id, ticket_id))
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        updated = replace(
            row,
            queue_id=queue_id if queue_id is not None else row.queue_id,
            status=status,
            updated_at=now,
            resolved_at=(
                now if status == "resolved"
                else row.resolved_at if status == "closed"
                else None
            ),
            human_intervened_at=(
                now if touch_human else row.human_intervened_at
            ),
        )
        self._current[(tenant_id, ticket_id)] = updated
        return updated

    def close_stale_resolved(self, *, older_than_hours: float = 48.0) -> list[str]:
        """Auto-close tickets that have been ``resolved`` longer than the
        threshold. Returns the closed ticket ids."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        closed: list[str] = []
        for (tid, ticket_id), row in list(self._current.items()):
            if (
                row.status == "resolved"
                and row.resolved_at is not None
                and row.resolved_at < cutoff
            ):
                self._current[(tid, ticket_id)] = replace(
                    row, status="closed", updated_at=datetime.now(timezone.utc)
                )
                closed.append(ticket_id)
        return closed

    def add_reply(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        author: str,
        body: str,
    ) -> TicketReply | None:
        if (tenant_id, ticket_id) not in self._current:
            return None
        reply = TicketReply(
            reply_id=uuid4(),
            author=author,
            body=body,
            created_at=datetime.now(timezone.utc),
        )
        self._replies.setdefault((tenant_id, ticket_id), []).append(reply)
        if author in {"agent", "customer"}:
            row = self._current.get((tenant_id, ticket_id))
            if row is not None and row.human_intervened_at is None:
                self._current[(tenant_id, ticket_id)] = replace(
                    row, human_intervened_at=reply.created_at
                )
        return reply

    def list_replies(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
    ) -> tuple[TicketReply, ...]:
        return tuple(self._replies.get((tenant_id, ticket_id), ()))

    def record_analysis(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        kind: str,
        canonical_version: int,
        payload: dict,
        model_id: str | None = None,
    ) -> TicketAnalysis | None:
        if (tenant_id, ticket_id) not in self._current:
            return None
        analysis = TicketAnalysis(
            analysis_id=uuid4(),
            kind=kind,
            canonical_version=canonical_version,
            payload=payload,
            model_id=model_id,
            created_at=datetime.now(timezone.utc),
        )
        self._analyses.setdefault((tenant_id, ticket_id), []).append(analysis)
        return analysis

    def mark_ticket_dedup(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        contact_id: str | None,
        symptom_fingerprint: str | None,
        dedup_of: str | None = None,
        text_fp: str | None = None,
    ) -> None:
        row = self._current.get((tenant_id, ticket_id))
        if row is None:
            return
        self._current[(tenant_id, ticket_id)] = replace(
            row,
            contact_id=contact_id,
            symptom_fingerprint=symptom_fingerprint,
            dedup_of=dedup_of,
            text_fp=text_fp,
        )

    def find_open_duplicate(
        self,
        *,
        tenant_id: str,
        contact_id: str,
        symptom_fingerprint: str | None = None,
        text_fp: str | None = None,
        exclude_ticket_id: str,
        window_hours: float = 72.0,
    ) -> str | None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        best: TicketCurrentRow | None = None
        for (tid, _tid), row in self._current.items():
            if tid != tenant_id or row.ticket_id == exclude_ticket_id:
                continue
            if row.contact_id != contact_id:
                continue
            fp_match = (
                (symptom_fingerprint and row.symptom_fingerprint == symptom_fingerprint)
                or (text_fp and row.text_fp == text_fp)
            )
            if not fp_match:
                continue
            if row.dedup_of is not None or row.status in {"resolved", "closed"}:
                continue
            if row.updated_at < cutoff:
                continue
            if best is None or row.updated_at > best.updated_at:
                best = row
        return best.ticket_id if best else None

    def latest_analysis(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        kind: str | None = None,
    ) -> TicketAnalysis | None:
        rows = [
            a for a in self._analyses.get((tenant_id, ticket_id), ())
            if kind is None or a.kind == kind
        ]
        return rows[-1] if rows else None

    def decisions_for_rows(self, rows) -> dict:
        out: dict = {}
        for r in rows:
            if r.current_decision_id is None:
                continue
            d = self.get_decision(
                tenant_id=r.tenant_id,
                event_id=r.event_id,
                canonical_version=r.canonical_version,
            )
            if d is not None:
                out[(r.tenant_id, r.current_decision_id)] = d
        return out

    def signal_priorities_for_rows(self, rows) -> dict:
        out: dict = {}
        for r in rows:
            a = self.latest_analysis(
                tenant_id=r.tenant_id, ticket_id=r.ticket_id, kind="llm_signals"
            )
            if a is None:
                continue
            pri = ((a.payload or {}).get("signals") or {}).get("priority")
            if pri:
                out[(r.tenant_id, r.ticket_id)] = str(pri).upper()
        return out

    def record_attachment_extractions(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        extractions: dict[UUID, AttachmentExtraction],
    ) -> None:
        for attachment_id, ext in extractions.items():
            self._attachment_extractions[
                (tenant_id, event_id, attachment_id)
            ] = ext

    def list_attachments(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
    ) -> tuple[dict, ...]:
        canonical = next(
            (
                e
                for (t, eid, _v), e in self._canonical.items()
                if t == tenant_id and eid == event_id
            ),
            None,
        )
        if canonical is None:
            return ()
        rows = []
        for att in canonical.attachments:
            manifest = self._attachment_manifests.get(
                (tenant_id, att.attachment_id, att.manifest_version)
            )
            ext = self._attachment_extractions.get(
                (tenant_id, event_id, att.attachment_id)
            )
            rows.append(
                {
                    "attachment_id": str(att.attachment_id),
                    "filename": ext.filename if ext else None,
                    "detected_media_type": att.detected_media_type,
                    "size_bytes": att.size_bytes,
                    "sha256": att.sha256,
                    "scan_status": att.scan_status.value,
                    "pii_class": att.pii_class.value,
                    "processing_status": att.processing_status.value,
                    "processor": ext.processor if ext else None,
                    "extracted_summary": ext.summary if ext else None,
                    "extracted_text": ext.extracted_text if ext else None,
                    "object_id": str(manifest.object_id) if manifest else None,
                }
            )
        return tuple(rows)

    def _record_raw_manifests(
        self,
        *,
        envelope: RawIntakeEnvelope,
        raw_body_manifest: ObjectManifest,
        attachment_manifests: Sequence[ObjectManifest],
        canonical: CanonicalTicketEvent,
    ) -> None:
        tenant_id = canonical.ticket.tenant_id
        raw_key = (tenant_id, envelope.event_id)
        if raw_key not in self._raw_events:
            self._raw_events[raw_key] = raw_body_manifest
        if len(attachment_manifests) != len(canonical.attachments):
            raise ValueError("attachment manifest count does not match canonical event")
        canonical_by_id = {a.attachment_id: a for a in canonical.attachments}
        for manifest in attachment_manifests:
            attachment = canonical_by_id.get(manifest.attachment_id)
            if attachment is None:
                raise ValueError("attachment manifest has no canonical attachment")
            key = (tenant_id, manifest.attachment_id, attachment.manifest_version)
            if key not in self._attachment_manifests:
                self._attachment_manifests[key] = manifest


@dataclass(frozen=True)
class PipelineResult:
    event: CanonicalTicketEvent
    decision: RoutingDecision
    ticket_current: TicketCurrentRow
    attachment_extractions: dict[UUID, AttachmentExtraction] = field(
        default_factory=dict
    )


class TenantUnresolvedError(RuntimeError):
    """Envelope tenant is unresolved/conflicting — pipeline fails closed."""


def _canonical_events_equivalent(
    stored: CanonicalTicketEvent,
    candidate: CanonicalTicketEvent,
) -> bool:
    """True when two canonical events differ only in version/timestamp."""
    left = stored.model_dump()
    right = candidate.model_dump()
    left["canonical_version"] = right["canonical_version"] = 0
    left["provenance"]["created_at"] = right["provenance"]["created_at"] = None
    return left == right


class TicketEventProcessor:
    """Drives one ingested event through extract → route → persist.

    Composition root supplies the store (in-memory or Postgres), the approved
    routing policy, and the manifest repository used by intake. The processor
    never trusts payload tenant/region fields.
    """

    def __init__(
        self,
        *,
        store: TicketPipelineStore,
        routing_policy: RoutingPolicy,
        routing_policy_data: dict,
        manifests: ObjectManifestRepository,
        object_store: ImmutableObjectStore | None = None,
    ) -> None:
        self._store = store
        self._routing_policy = routing_policy
        self._routing_policy_data = routing_policy_data
        self._manifests = manifests
        self._object_store = object_store

    def process(
        self,
        *,
        intake_result: IntakeResult,
        p1: bool = False,
        ticket_status: str = "open",
        ticket_age_hours: float | None = None,
        route_context: str = "",
    ) -> PipelineResult:
        envelope = intake_result.envelope
        resolution = envelope.tenant_resolution
        if (
            resolution.status is not TenantResolutionStatus.RESOLVED
            or resolution.tenant is None
        ):
            raise TenantUnresolvedError(
                f"tenant resolution status is {resolution.status.value}",
            )
        tenant_id = resolution.tenant.tenant_id
        region = resolution.tenant.region

        raw_body = self._manifests.get_for_tenant(
            object_id=envelope.raw_body_ref.object_id,
            tenant_id=tenant_id,
        )
        attachment_manifests = tuple(
            self._manifests.get_for_tenant(
                object_id=receipt.object_id,
                tenant_id=tenant_id,
            )
            for receipt in envelope.attachment_receipts
        )

        extraction, payload_p1 = extract_canonical_envelope(envelope)
        extraction, attachment_blocked, attachment_extractions = (
            self._scan_attachments(
                envelope=envelope,
                attachment_manifests=attachment_manifests,
                extraction=extraction,
            )
        )

        latest_version = self._store.latest_canonical_version(
            tenant_id=tenant_id,
            event_id=envelope.event_id,
        )
        # Pub/Sub is at-least-once: an identical redelivery must not create a
        # new canonical version. Compare everything except version/created_at.
        if latest_version:
            latest = self._store.get_canonical(
                tenant_id=tenant_id,
                event_id=envelope.event_id,
                canonical_version=latest_version,
            )
            if latest is not None:
                replay = build_canonical_event(
                    envelope=envelope,
                    extraction=extraction,
                    canonical_version=latest.canonical_version,
                )
                if _canonical_events_equivalent(latest, replay):
                    decision = self._store.get_decision(
                        tenant_id=tenant_id,
                        event_id=envelope.event_id,
                        canonical_version=latest.canonical_version,
                    )
                    current = self._store.get_ticket_current(
                        tenant_id=tenant_id,
                        ticket_id=latest.ticket.ticket_id,
                    )
                    if decision is not None and current is not None:
                        return PipelineResult(
                            event=latest,
                            decision=decision,
                            ticket_current=current,
                        )

        canonical_version = latest_version + 1
        fingerprint = content_fingerprint(
            tenant_id=tenant_id,
            raw_sha256=envelope.raw_body_ref.sha256,
            canonical_version=canonical_version,
        )
        text = f"{extraction.subject_masked}\n{extraction.body_masked}"
        # Modality processors feed bounded extracted text (PDF payslip
        # lines, screenshot error codes, call transcripts) into the same
        # deterministic rule base — attachments are first-class evidence.
        att_snippets = [
            e.route_snippet
            for e in attachment_extractions.values()
            if e.route_snippet
        ]
        if att_snippets:
            text = f"{text}\n[attachments]\n" + "\n".join(att_snippets)[:2000]
        # pivot_en: the LLM's English restatement lets English keyword
        # rules classify non-English tickets deterministically.
        if route_context:
            text = f"{text}\n[pivot_en]\n{route_context[:500]}"
        candidate, blocking_risk = routing_candidate_from_approved_rules(
            text=text,
            region=region.value,
            p1=p1 or payload_p1,
            policy=self._routing_policy_data,
            tenant_id=tenant_id,
        )
        decision = self._routing_policy.decide(
            tenant_id=tenant_id,
            event_id=envelope.event_id,
            canonical_version=canonical_version,
            input_fingerprint=fingerprint,
            candidate=candidate,
            tenant_resolved=True,
            schema_valid=True,
            blocking_risk=blocking_risk or attachment_blocked,
        )

        # AUTO_ANSWER lane tickets are bot-resolved end-to-end; callers can
        # still force a different initial status explicitly.
        effective_status = (
            "resolved"
            if decision.lane is Lane.AUTO_ANSWER and ticket_status == "open"
            else ticket_status
        )
        event = self._store.record_ticket_event(
            envelope=envelope,
            raw_body_manifest=raw_body,
            attachment_manifests=attachment_manifests,
            extraction=extraction,
            decision=decision,
            ticket_status=effective_status,
            updated_at=(
                datetime.now(timezone.utc)
                - timedelta(hours=ticket_age_hours)
                if ticket_age_hours is not None
                else None
            ),
        )
        current = self._store.get_ticket_current(
            tenant_id=tenant_id,
            ticket_id=event.ticket.ticket_id,
        )
        if current is None:  # pragma: no cover - store contract guarantees row
            raise RuntimeError("ticket_current projection missing after record")
        return PipelineResult(
            event=event,
            decision=decision,
            ticket_current=current,
            attachment_extractions=attachment_extractions,
        )

    def _scan_attachments(
        self,
        *,
        envelope: RawIntakeEnvelope,
        attachment_manifests: tuple[ObjectManifest, ...],
        extraction: CanonicalExtraction,
    ) -> tuple[CanonicalExtraction, bool, dict[UUID, AttachmentExtraction]]:
        """DLP/malware gate + modality extraction when a store is configured.

        Returns the extraction with per-attachment assessments merged in,
        whether any attachment was blocked (forces human review downstream),
        and the per-attachment processor output for persistence/routing.
        """
        if self._object_store is None or not attachment_manifests:
            return extraction, False, {}

        receipts = {r.attachment_id: r for r in envelope.attachment_receipts}
        assessments = dict(extraction.attachment_assessments)
        extractions: dict[UUID, AttachmentExtraction] = {}
        blocked = False
        for manifest in attachment_manifests:
            receipt = receipts.get(manifest.attachment_id)
            if receipt is None:
                continue
            body = self._object_store.read_internal(manifest.object_path)
            assessment = scan_attachment(
                body=body,
                filename=receipt.original_filename,
                claimed_media_type=receipt.claimed_media_type,
            )
            assessments[manifest.attachment_id] = assessment
            if assessment.scan_status is ScanStatus.BLOCKED:
                blocked = True
            extractions[manifest.attachment_id] = extract_attachment(
                body=body,
                filename=receipt.original_filename,
                claimed_media_type=receipt.claimed_media_type,
                detected_media_type=assessment.detected_media_type,
            )
        return (
            extraction.model_copy(update={"attachment_assessments": assessments}),
            blocked,
            extractions,
        )
