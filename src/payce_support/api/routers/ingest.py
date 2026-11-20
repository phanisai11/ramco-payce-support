"""Immutable intake — threaded under concurrency semaphore (async API)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException

from payce_support.api.deps import AppStateDep, require_authenticated_v1
from payce_support.api.schemas import IngestHttpRequest, IngestHttpResponse
from payce_support.intake import IdempotencyConflictError

router = APIRouter(
    prefix="/v1/intake",
    tags=["intake"],
    dependencies=[Depends(require_authenticated_v1)],
)


@router.post("/events", response_model=IngestHttpResponse)
async def ingest_event(state: AppStateDep, body: IngestHttpRequest) -> IngestHttpResponse:
    ctx = body.to_context()
    cmd = body.to_command()

    async with state.intake_semaphore:
        try:
            result = await asyncio.to_thread(state.intake.ingest, context=ctx, command=cmd)
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    env_dict = json.loads(result.envelope.model_dump_json(by_alias=True))
    return IngestHttpResponse(event_id=str(result.envelope.event_id), envelope_json=env_dict)
