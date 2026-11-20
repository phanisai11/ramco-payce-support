"""Fail-closed routing policy and append-only decision repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from payce_support.models import DecidedBy, Lane, RoutingDecision


class RoutingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    lane: Lane
    queue_id: str | None = Field(default=None, max_length=128)
    decided_by: DecidedBy
    rule_id: str | None = Field(default=None, max_length=128)
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=50)
    confidence: float = Field(ge=0, le=1)


class RoutingDecisionRepository(Protocol):
    def append(self, decision: RoutingDecision) -> None: ...

    def get(self, decision_id: UUID, *, tenant_id: str) -> RoutingDecision: ...

    def get_for_event(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        canonical_version: int,
    ) -> RoutingDecision | None: ...


class InMemoryRoutingDecisionRepository:
    def __init__(self) -> None:
        self._decisions: dict[UUID, RoutingDecision] = {}

    def append(self, decision: RoutingDecision) -> None:
        if decision.decision_id in self._decisions:
            raise ValueError("routing decisions are append-only")
        if (
            decision.supersedes_decision_id is not None
            and decision.supersedes_decision_id not in self._decisions
        ):
            raise ValueError("superseded routing decision does not exist")
        self._decisions[decision.decision_id] = decision

    def get(self, decision_id: UUID, *, tenant_id: str) -> RoutingDecision:
        decision = self._decisions[decision_id]
        if decision.tenant_id != tenant_id:
            raise KeyError(decision_id)
        return decision

    def get_for_event(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        canonical_version: int,
    ) -> RoutingDecision | None:
        for decision in self._decisions.values():
            if (
                decision.tenant_id == tenant_id
                and decision.event_id == event_id
                and decision.canonical_version == canonical_version
            ):
                return decision
        return None


class RoutingPolicy:
    def __init__(
        self,
        *,
        policy_version: str,
        approved_rule_ids: set[str],
        repository: RoutingDecisionRepository,
    ) -> None:
        self._policy_version = policy_version
        self._approved_rule_ids = frozenset(approved_rule_ids)
        self._repository = repository

    def reload(self, *, policy_version: str, approved_rule_ids: set[str]) -> None:
        """Refresh G3 approval set + version after an approved.yaml edit so
        hot-reloads take effect without a restart."""
        self._policy_version = policy_version
        self._approved_rule_ids = frozenset(approved_rule_ids)

    def decide(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        canonical_version: int,
        input_fingerprint: str,
        candidate: RoutingCandidate,
        tenant_resolved: bool,
        schema_valid: bool,
        blocking_risk: bool,
        supersedes_decision_id: UUID | None = None,
    ) -> RoutingDecision:
        deterministic_deciders = {
            DecidedBy.RULE,
            DecidedBy.POLICY_TABLE,
            DecidedBy.ROUTING_SCORER,
        }
        auto_route = all(
            (
                tenant_resolved,
                schema_valid,
                not blocking_risk,
                candidate.decided_by in deterministic_deciders,
                candidate.rule_id in self._approved_rule_ids,
                candidate.queue_id is not None,
            ),
        )
        reason_codes = list(candidate.reason_codes)
        if not auto_route:
            reason_codes.append(self._abstain_reason(
                candidate=candidate,
                tenant_resolved=tenant_resolved,
                schema_valid=schema_valid,
                blocking_risk=blocking_risk,
            ))

        decision = RoutingDecision(
            decision_id=uuid4(),
            tenant_id=tenant_id,
            event_id=event_id,
            canonical_version=canonical_version,
            input_fingerprint=input_fingerprint,
            policy_version=self._policy_version,
            lane=candidate.lane if auto_route else Lane.HUMAN,
            queue_id=candidate.queue_id if auto_route else None,
            auto_route=auto_route,
            decided_by=candidate.decided_by,
            rule_id=candidate.rule_id,
            reason_codes=tuple(reason_codes),
            confidence=candidate.confidence,
            supersedes_decision_id=supersedes_decision_id,
            created_at=datetime.now(timezone.utc),
        )
        self._repository.append(decision)
        return decision

    def _abstain_reason(
        self,
        *,
        candidate: RoutingCandidate,
        tenant_resolved: bool,
        schema_valid: bool,
        blocking_risk: bool,
    ) -> str:
        if not tenant_resolved:
            return "ABSTAIN_TENANT_UNRESOLVED"
        if not schema_valid:
            return "ABSTAIN_SCHEMA_INVALID"
        if blocking_risk:
            return "ABSTAIN_BLOCKING_RISK"
        if candidate.decided_by in {DecidedBy.LLM, DecidedBy.SUGGEST}:
            return "ABSTAIN_NON_AUTHORITATIVE_DECIDER"
        if candidate.rule_id not in self._approved_rule_ids:
            return "ABSTAIN_RULE_NOT_G3_APPROVED"
        if candidate.queue_id is None:
            return "ABSTAIN_QUEUE_MISSING"
        return "ABSTAIN_POLICY"
