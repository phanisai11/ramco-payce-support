"""FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from payce_support.api.auth import verify_request_token
from payce_support.api.state import AppState


def get_app_state(request: Request) -> AppState:
    st = getattr(request.app.state, "payce", None)
    if st is None:
        raise RuntimeError("Application state not initialized")
    return st


AppStateDep = Annotated[AppState, Depends(get_app_state)]


def require_authenticated_v1(request: Request) -> None:
    """When `PAYCE_AUTH_MODE` is not `none`, validate JWT / IAP before `/v1/*`."""
    state = get_app_state(request)
    caller = verify_request_token(state.settings, request)
    if caller is not None:
        request.state.payce_caller = caller
