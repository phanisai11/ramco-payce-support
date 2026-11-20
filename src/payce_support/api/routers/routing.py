from __future__ import annotations

import asyncio
from hashlib import sha256
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from payce_support.ai_contracts import AiCallError
from payce_support.api.deps import AppStateDep, require_authenticated_v1
from payce_support.api.schemas import (
    TICKET_STATUSES,
    RoutingAssistInfo,
    RoutingClassifyRequest,
    RoutingClassifyResponse,
    RoutingPolicyResponse,
    RoutingPolicyRule,
    RoutingRuleUpdateRequest,
    TenantContextUpsertRequest,
)
from payce_support.models import Region
from payce_support.routing_matcher import (
    rule_status,
    routing_candidate_from_approved_rules,
)
from payce_support.routing_signals import RoutingSignals

router = APIRouter(
    prefix="/v1/routing",
    tags=["routing"],
    dependencies=[Depends(require_authenticated_v1)],
)

_LLM_TIMEOUT_SECONDS = 8.0


@router.post("/classify", response_model=RoutingClassifyResponse)
async def classify(state: AppStateDep, body: RoutingClassifyRequest) -> RoutingClassifyResponse:
    assist_wanted = (
        body.assist if body.assist is not None else state.settings.routing_llm_enabled
    )
    signals: RoutingSignals | None = None
    assist: RoutingAssistInfo | None = None

    if assist_wanted:
        extractor = state.signal_extractor
        if extractor is None:
            assist = RoutingAssistInfo(used=False, degraded_reason="LLM_NOT_CONFIGURED")
        else:
            try:
                signals = await asyncio.wait_for(
                    extractor.extract(
                        text=body.text,
                        region=Region(body.region),
                        tenant_id=body.tenant_id,
                    ),
                    timeout=_LLM_TIMEOUT_SECONDS,
                )
                assist = RoutingAssistInfo(
                    used=True,
                    p1=signals.p1,
                    priority=signals.priority,
                    entities=signals.entities,
                    intent=signals.intent,
                    confidence=signals.confidence,
                    model_id=extractor.model_id,
                    summary=signals.summary or None,
                    rag_hints=signals.rag_hints,
                )
            except (AiCallError, ValidationError, ValueError, TimeoutError) as exc:
                assist = RoutingAssistInfo(
                    used=False,
                    degraded_reason=type(exc).__name__,
                )

    candidate, blocking_risk = routing_candidate_from_approved_rules(
        text=body.text,
        region=body.region,
        p1=body.p1,
        policy=state.routing_policy_data,
        signals=signals,
        tenant_id=body.tenant_id,
    )
    fp = sha256(body.text.encode("utf-8")).hexdigest()
    decision = state.routing_policy.decide(
        tenant_id=body.tenant_id,
        event_id=uuid4(),
        canonical_version=1,
        input_fingerprint=fp,
        candidate=candidate,
        tenant_resolved=True,
        schema_valid=True,
        blocking_risk=blocking_risk,
    )
    return RoutingClassifyResponse(
        auto_route=decision.auto_route,
        queue_id=decision.queue_id,
        lane=decision.lane.value,
        rule_id=decision.rule_id,
        reason_codes=decision.reason_codes,
        decided_by=decision.decided_by.value,
        assist=assist,
    )


@router.get("/policy", response_model=RoutingPolicyResponse)
def routing_policy(state: AppStateDep) -> RoutingPolicyResponse:
    """Approved rules + per-tenant LLM context, for UI/audit display."""
    data = state.routing_policy_data
    rules = tuple(
        RoutingPolicyRule(
            id=str(rule["id"]),
            queue_id=str(rule["queue_id"]),
            lane=str(rule["lane"]),
            region=rule.get("region"),
            tenant_id=rule.get("tenant_id"),
            valid_from=(
                str(rule["valid_from"]) if rule.get("valid_from") else None
            ),
            valid_to=(
                str(rule["valid_to"]) if rule.get("valid_to") else None
            ),
            pattern=str(rule["pattern"]),
            signals=tuple(str(s) for s in rule.get("signals") or ()),
            reason_codes=tuple(str(s) for s in rule.get("reason_codes") or ()),
            status=rule_status(rule),
        )
        for rule in data["rules"]
    )
    return RoutingPolicyResponse(
        policy_version=str(data["policy_version"]),
        approved_rule_ids=tuple(str(x) for x in data["approved_rule_ids"]),
        rules=rules,
        ticket_statuses=TICKET_STATUSES,
        tenant_contexts={
            tid: ctx.model_dump(mode="json")
            for tid, ctx in state.tenant_contexts.items()
        },
    )


@router.put("/rules/{rule_id}", response_model=RoutingPolicyRule)
def update_routing_rule(
    state: AppStateDep,
    rule_id: str,
    body: RoutingRuleUpdateRequest,
) -> RoutingPolicyRule:
    """Edit an approved routing rule and persist to approved.yaml."""
    from payce_support.policy_editor import update_yaml_rule

    path = state.routing_policy_path
    if path is None:
        raise HTTPException(status_code=503, detail="routing policy file not configured")
    updates = {
        k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None
    }
    # Allow clearing optional fields explicitly.
    for field_name in ("tenant_id", "region", "valid_from", "valid_to"):
        if field_name in body.model_fields_set and getattr(body, field_name) is None:
            updates[field_name] = None
    try:
        data = update_yaml_rule(path, rule_id=rule_id, updates=updates)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Hot-reload: mutate the shared policy dict in place so the processor,
    # matcher, and policy endpoint all see the update immediately — and
    # refresh the G3 approval set so newly approved rule ids take effect.
    state.routing_policy_data.clear()
    state.routing_policy_data.update(data)
    state.routing_policy.reload(
        policy_version=str(data.get("policy_version") or ""),
        approved_rule_ids=set(data.get("approved_rule_ids") or ()),
    )
    rule = next(r for r in data["rules"] if r["id"] == rule_id)
    return RoutingPolicyRule(
        id=str(rule["id"]),
        queue_id=str(rule["queue_id"]),
        lane=str(rule["lane"]),
        region=rule.get("region"),
        tenant_id=rule.get("tenant_id"),
        valid_from=(str(rule["valid_from"]) if rule.get("valid_from") else None),
        valid_to=(str(rule["valid_to"]) if rule.get("valid_to") else None),
        pattern=str(rule["pattern"]),
        signals=tuple(str(s) for s in rule.get("signals") or ()),
        reason_codes=tuple(str(s) for s in rule.get("reason_codes") or ()),
        status=rule_status(rule),
    )


@router.put("/tenant-contexts/{tenant_id}", response_model=dict)
def upsert_tenant_context(
    state: AppStateDep,
    tenant_id: str,
    body: TenantContextUpsertRequest,
) -> dict:
    """Create/update a tenant's LLM routing context and persist to
    ``routing_policy/tenant_context/<tenant_id>.yaml``. Hot-reloads
    ``state.tenant_contexts`` so the signal extractor sees it immediately."""
    import yaml

    from payce_support.tenant_context import (
        TenantRoutingContext,
        load_tenant_contexts,
    )

    try:
        ctx = TenantRoutingContext.model_validate(
            {
                **body.model_dump(mode="json", exclude_none=True),
                "tenant_id": tenant_id,
            }
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    directory = state.settings.tenant_context_dir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{tenant_id}.yaml"
    data = ctx.model_dump(mode="json", exclude_none=True)
    # Tuples serialize as !!python/tuple otherwise — normalize to lists.
    def _plain(v):
        if isinstance(v, (list, tuple)):
            return [_plain(x) for x in v]
        if isinstance(v, dict):
            return {k: _plain(x) for k, x in v.items()}
        return v
    path.write_text(
        yaml.safe_dump(_plain(data), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    fresh = load_tenant_contexts(directory)
    state.tenant_contexts.clear()
    state.tenant_contexts.update(fresh)
    return ctx.model_dump(mode="json")
