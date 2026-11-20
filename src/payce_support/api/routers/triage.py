"""LangGraph triage endpoint — orchestrated inbound message handling.

Runs the intake-triage StateGraph: mask → extract signals → missing-field
check → deterministic route → follow-up / escalate / draft reply.
Orchestration only — does NOT persist tickets (use /v1/pipeline/process
for that). LLM nodes describe/draft; routing decisions stay deterministic.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from payce_support.api.deps import AppStateDep, require_authenticated_v1
from payce_support.api.schemas import TriageRequest, TriageResponse

router = APIRouter(
    prefix="/v1/triage",
    tags=["triage"],
    dependencies=[Depends(require_authenticated_v1)],
)


@router.post("", response_model=TriageResponse)
async def triage(state: AppStateDep, body: TriageRequest) -> TriageResponse:
    flow = state.triage_flow
    if flow is None:
        raise HTTPException(
            status_code=503,
            detail="triage flow unavailable (langgraph not installed)",
        )
    result = await flow.triage(
        text=body.text,
        region=body.region,
        tenant_id=body.tenant_id,
        p1=body.p1,
    )
    return TriageResponse(
        outcome=result.get("outcome"),
        reply=result.get("reply"),
        missing=tuple(result.get("missing") or ()),
        decision=result.get("decision"),
        candidate=result.get("candidate"),
        analysis_status=result.get("analysis_status"),
        signals=result.get("signals"),
    )
