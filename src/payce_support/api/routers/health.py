from __future__ import annotations

from fastapi import APIRouter

from payce_support.api.deps import AppStateDep
from payce_support.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(_state: AppStateDep) -> HealthResponse:
    return HealthResponse()
