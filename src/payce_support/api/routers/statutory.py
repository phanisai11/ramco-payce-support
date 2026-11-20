from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from payce_support.api.deps import AppStateDep, require_authenticated_v1
from payce_support.api.schemas import StatutoryResolveQuery, StatutoryResolveResponse

router = APIRouter(
    prefix="/v1/statutory",
    tags=["statutory"],
    dependencies=[Depends(require_authenticated_v1)],
)


@router.get("/resolve", response_model=StatutoryResolveResponse)
def resolve_statutory(
    state: AppStateDep,
    q: StatutoryResolveQuery = Depends(),
) -> StatutoryResolveResponse:
    try:
        r = state.statutory.resolve(region=q.region, topic=q.topic, as_of=q.as_of)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StatutoryResolveResponse.from_resolution(r)
