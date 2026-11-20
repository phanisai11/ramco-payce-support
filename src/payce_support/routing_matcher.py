"""Deterministic routing candidates from approved YAML rules (G3 — no vectors).

PyYAML load: https://pyyaml.org/wiki/PyYAMLDocumentation
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from payce_support.models import DecidedBy, Lane
from payce_support.routing import RoutingCandidate
from payce_support.routing_signals import RoutingSignals


class ApprovedRoutingPolicyLoadError(RuntimeError):
    pass


def load_approved_routing_policy(path: Path | str) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ApprovedRoutingPolicyLoadError("approved routing policy root must be a mapping")
    approved = data.get("approved_rule_ids")
    if not isinstance(approved, list):
        raise ApprovedRoutingPolicyLoadError("approved_rule_ids must be a list")
    rules = data.get("rules")
    if not isinstance(rules, list):
        raise ApprovedRoutingPolicyLoadError("rules must be a list")
    seen: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ApprovedRoutingPolicyLoadError("each rule must be a mapping")
        rid = rule.get("id")
        if not isinstance(rid, str):
            raise ApprovedRoutingPolicyLoadError("rule id must be a string")
        if rid in seen:
            raise ApprovedRoutingPolicyLoadError(f"duplicate rule id: {rid}")
        seen.add(rid)
        if rid not in approved:
            raise ApprovedRoutingPolicyLoadError(f"rule {rid!r} not in approved_rule_ids")
    for aid in approved:
        if aid not in seen:
            raise ApprovedRoutingPolicyLoadError(f"approved_rule_id {aid!r} has no rule block")
    return data


def _rule_window(rule: dict[str, Any]) -> tuple[date | None, date | None]:
    """Optional valid_from/valid_to (ISO dates) bounding a rule's lifetime."""
    start = rule.get("valid_from")
    end = rule.get("valid_to")
    return (
        date.fromisoformat(str(start)) if start else None,
        date.fromisoformat(str(end)) if end else None,
    )


def rule_status(rule: dict[str, Any], *, today: date | None = None) -> str:
    """Lifecycle status for display: expired windows win over stored status."""
    _start, end = _rule_window(rule)
    if end is not None and end < (today or datetime.now(timezone.utc).date()):
        return "expired"
    return str(rule.get("status") or "approved")


def _rule_applicable(
    rule: dict[str, Any],
    *,
    region: str,
    tenant_id: str | None,
    today: date,
) -> bool:
    rule_region = rule.get("region")
    if isinstance(rule_region, str) and rule_region != region:
        return False
    rule_tenant = rule.get("tenant_id")
    if isinstance(rule_tenant, str) and rule_tenant != tenant_id:
        return False
    start, end = _rule_window(rule)
    if start is not None and today < start:
        return False
    if end is not None and today > end:
        return False
    return True


def routing_candidate_from_approved_rules(
    *,
    text: str,
    region: str,
    p1: bool,
    policy: dict[str, Any],
    signals: RoutingSignals | None = None,
    tenant_id: str | None = None,
    as_of: date | None = None,
) -> tuple[RoutingCandidate, bool]:
    """Return (candidate, blocking_risk). P1 always blocks auto-route via policy.decide(blocking_risk=True).

    ``signals`` are LLM-extracted hints: they can escalate to P1 and can
    trigger a rule via its ``signals`` aliases, but they can never create a
    route that no approved rule sanctions.
    """
    llm_p1 = signals is not None and (
        signals.p1 or signals.priority == "p1"
    )
    if p1 or llm_p1:
        codes = ["P1_ESCALATION"]
        if llm_p1 and not p1:
            codes.append("LLM_P1_FLAG")
        return (
            RoutingCandidate(
                lane=Lane.HUMAN,
                queue_id=None,
                decided_by=DecidedBy.RULE,
                rule_id=None,
                reason_codes=tuple(codes),
                confidence=1.0,
            ),
            True,
        )

    # P6 — prompt-injection screen runs before any rule matching: hostile
    # text goes human-only with a security reason code, never auto-routed.
    from payce_support.extraction import screen_injection

    if screen_injection(text):
        return (
            RoutingCandidate(
                lane=Lane.HUMAN,
                queue_id=None,
                decided_by=DecidedBy.RULE,
                rule_id=None,
                reason_codes=("INJECTION_FLAGGED",),
                confidence=1.0,
            ),
            True,
        )

    entities = set(signals.entities) if signals is not None else set()
    today = as_of or datetime.now(timezone.utc).date()
    rules = policy["rules"]
    for rule in rules:
        if not _rule_applicable(
            rule, region=region, tenant_id=tenant_id, today=today
        ):
            continue
        pattern_match = re.search(rule["pattern"], text) is not None
        signal_match = bool(entities & set(rule.get("signals") or ()))
        if pattern_match or signal_match:
            lane_name = str(rule["lane"])
            try:
                lane = Lane[lane_name]
            except KeyError as exc:
                raise ApprovedRoutingPolicyLoadError(
                    f"unknown lane {lane_name!r} on rule {rule.get('id')!r}",
                ) from exc
            reason_codes = list(rule.get("reason_codes") or ("RULE_MATCH",))
            if signal_match and not pattern_match:
                reason_codes.append("LLM_SIGNAL_MATCH")
            return (
                RoutingCandidate(
                    lane=lane,
                    queue_id=str(rule["queue_id"]),
                    decided_by=DecidedBy.RULE,
                    rule_id=str(rule["id"]),
                    reason_codes=tuple(str(x) for x in reason_codes),
                    confidence=float(rule.get("confidence", 1.0)),
                ),
                False,
            )

    return (
        RoutingCandidate(
            lane=Lane.HUMAN,
            queue_id=None,
            decided_by=DecidedBy.RULE,
            rule_id=None,
            reason_codes=("NO_APPROVED_RULE_MATCH",),
            confidence=0.0,
        ),
        False,
    )
