from __future__ import annotations

from fastapi import APIRouter, Depends

from payce_support.api.deps import AppStateDep, require_authenticated_v1
from payce_support.api.schemas import IncidentSignalRequest, IncidentSignalResponse
from payce_support.incident_cluster import ClusterTicketSignal, symptom_fingerprint_from_text

router = APIRouter(
    prefix="/v1/incident",
    tags=["incident"],
    dependencies=[Depends(require_authenticated_v1)],
)


@router.post("/signals", response_model=IncidentSignalResponse)
async def post_signal(state: AppStateDep, body: IncidentSignalRequest) -> IncidentSignalResponse:
    fp = symptom_fingerprint_from_text(
        error_code=body.error_code,
        subject=body.subject,
        body=body.body,
    )
    signal = ClusterTicketSignal(
        received_at=body.received_at,
        symptom_fingerprint=fp,
        tenant_id=body.tenant_id,
        channel=body.channel,
    )

    async with state.incident_lock:
        decl = state.incident.on_signal(signal)

    if decl is None:
        return IncidentSignalResponse(declared=False, symptom_fingerprint=fp)
    return IncidentSignalResponse(
        declared=True,
        symptom_fingerprint=decl.symptom_fingerprint,
        fifth_ticket_at=decl.fifth_ticket_at,
        declared_at=decl.declared_at,
    )


@router.get("/recent")
async def recent_incidents(state: AppStateDep) -> dict:
    """Declared incidents from the cluster engine (pipeline-fed)."""
    return {
        "incidents": [
            {
                "symptom_fingerprint": d.symptom_fingerprint,
                "tenant_id": d.tenant_id,
                "ticket_count": d.ticket_count,
                "channel": d.channel,
                "ticket_ids": list(d.ticket_ids),
                "fifth_ticket_at": d.fifth_ticket_at.isoformat(),
                "declared_at": d.declared_at.isoformat(),
            }
            for d in state.incident.recent()
        ]
    }
