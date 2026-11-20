"""Optional API authentication — dev stays open unless PAYCE_AUTH_MODE is set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Never

import jwt
from fastapi import HTTPException, Request

from payce_support.api.settings import ApiAuthMode, ApiSettings

_IAP_ISSUER = "https://cloud.google.com/iap"
_GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"


@dataclass(frozen=True)
class VerifiedCaller:
    """Best-effort identity from verified JWT (optional observability)."""

    sub: str | None
    email: str | None


def _bearer_from_authorization(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    prefix = "Bearer "
    if not header.startswith(prefix):
        return None
    token = header[len(prefix) :].strip()
    return token or None


def _extract_token(settings: ApiSettings, request: Request) -> str:
    if settings.auth_mode == "iap":
        iap_h = request.headers.get("x-goog-iap-jwt-assertion")
        if iap_h and iap_h.strip():
            return iap_h.strip()
    bearer = _bearer_from_authorization(request)
    if bearer:
        return bearer
    raise HTTPException(status_code=401, detail="missing credentials")


def _decode_jwt_hs256(*, token: str, settings: ApiSettings) -> VerifiedCaller:
    if not settings.jwt_secret:
        raise RuntimeError("PAYCE_AUTH_MODE=jwt_hs256 requires PAYCE_JWT_SECRET")
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": settings.jwt_audience is not None,
                "verify_iss": settings.jwt_issuer is not None,
            },
        )
    except jwt.exceptions.PyJWTError as exc:  # noqa: BLE001 — map to 401
        raise HTTPException(status_code=401, detail="invalid token") from exc
    return VerifiedCaller(sub=payload.get("sub"), email=payload.get("email"))


def _decode_jwt_rs256(*, token: str, settings: ApiSettings) -> VerifiedCaller:
    pem = settings.jwt_public_key_pem
    if not pem:
        raise RuntimeError("PAYCE_AUTH_MODE=jwt_rs256 requires PAYCE_JWT_PUBLIC_KEY_PEM "
                           "or PAYCE_JWT_PUBLIC_KEY_PATH")
    try:
        payload = jwt.decode(
            token,
            pem,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": settings.jwt_audience is not None,
                "verify_iss": settings.jwt_issuer is not None,
            },
        )
    except jwt.exceptions.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    return VerifiedCaller(sub=payload.get("sub"), email=payload.get("email"))


def _decode_iap_jwt(*, token: str, settings: ApiSettings) -> VerifiedCaller:
    if not settings.iap_audience:
        raise RuntimeError("PAYCE_AUTH_MODE=iap requires PAYCE_IAP_AUDIENCE")
    try:
        jwks_client = jwt.PyJWKClient(_GOOGLE_JWKS_URL)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.iap_audience,
            issuer=_IAP_ISSUER,
        )
    except jwt.exceptions.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid IAP token") from exc
    return VerifiedCaller(sub=payload.get("sub"), email=payload.get("email"))


def verify_request_token(settings: ApiSettings, request: Request) -> VerifiedCaller | None:
    if settings.auth_mode == "none":
        return None
    token = _extract_token(settings, request)
    mode: ApiAuthMode = settings.auth_mode
    if mode == "jwt_hs256":
        return _decode_jwt_hs256(token=token, settings=settings)
    if mode == "jwt_rs256":
        return _decode_jwt_rs256(token=token, settings=settings)
    if mode == "iap":
        return _decode_iap_jwt(token=token, settings=settings)
    unreachable: Never = mode  # type: ignore[assignment]
    raise RuntimeError(f"unknown auth mode {unreachable!r}")
