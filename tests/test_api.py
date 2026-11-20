"""HTTP API smoke tests — composition root and concurrency boundary."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from payce_support.api.app import create_app
from payce_support.api.settings import ApiSettings

_UTC = timezone.utc


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_serves_react_app(client: TestClient) -> None:
    # "/" serves the built React SPA (web/app/dist) when present; the
    # legacy JS dashboard remains at /dev/ui/.
    import os
    if not os.path.isdir("web/app/dist"):
        pytest.skip("web/app/dist not built")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200


def test_dev_ui_index_served(client: TestClient) -> None:
    r = client.get("/dev/ui/")
    assert r.status_code == 200
    assert b"Payce Support Intelligence" in r.content


def test_statutory_resolve_pf_ceiling(client: TestClient) -> None:
    r = client.get(
        "/v1/statutory/resolve",
        params={"region": "IN", "topic": "pf_wage_ceiling", "as_of": "2026-08-15"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rule_id"] == "R-IN-PF-2026-08"
    assert "25,000" in body["value_applied"]


def test_routing_classify_hits_approved_rule(client: TestClient) -> None:
    r = client.post(
        "/v1/routing/classify",
        json={
            "tenant_id": "C-1001",
            "text": "Question on EPF contribution statutory ceiling India",
            "region": "IN",
            "p1": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["auto_route"] is True
    assert body["rule_id"] == "route-c1001-statutory-pf-2026"


def test_incident_declares_on_fifth(client: TestClient) -> None:
    payload = {
        "tenant_id": "C-1001",
        "channel": "email",
        "received_at": "2026-09-25T10:00:00Z",
        "error_code": "PAYSLIP_RENDER_EMPTY",
        "subject": "Blank payslip",
        "body": "PDF is empty for Meridian",
    }
    for i in range(4):
        payload_i = {
            **payload,
            "received_at": f"2026-09-25T10:0{i}:00Z",
        }
        r = client.post("/v1/incident/signals", json=payload_i)
        assert r.status_code == 200
        assert r.json()["declared"] is False

    r5 = client.post(
        "/v1/incident/signals",
        json={**payload, "received_at": "2026-09-25T10:04:00Z"},
    )
    assert r5.status_code == 200
    assert r5.json()["declared"] is True


def test_ingest_returns_envelope(client: TestClient) -> None:
    body = {
        "principal_id": "p1",
        "channel_account_id": "widget-in",
        "ingress_region": "IN",
        "authenticated_tenant_id": "C-1001",
        "verified_contact_id": None,
        "source": "widget",
        "source_event_id": "api-test-evt-1",
        "received_at": "2026-11-06T09:00:00Z",
        "source_occurred_at": "2026-11-06T08:59:00Z",
        "payload_schema": "widget/v1",
        "raw_body_base64": base64.b64encode(b'{"q":1}').decode("ascii"),
        "parsed_payload": {"q": 1},
        "attachments": (),
    }
    r = client.post("/v1/intake/events", json=body)
    assert r.status_code == 200
    data = r.json()
    assert "event_id" in data
    assert data["envelope_json"]["schema"] == "com.payce.intake.raw.v1"


def test_v1_requires_bearer_when_auth_mode_jwt_hs256() -> None:
    old_mode = os.environ.get("PAYCE_AUTH_MODE")
    old_secret = os.environ.get("PAYCE_JWT_SECRET")
    try:
        os.environ["PAYCE_AUTH_MODE"] = "jwt_hs256"
        os.environ["PAYCE_JWT_SECRET"] = "test-secret-at-least-32-bytes-long!!"
        settings = ApiSettings.from_environ()
        app = create_app(settings=settings)
        with TestClient(app) as client:
            r = client.get(
                "/v1/statutory/resolve",
                params={"region": "IN", "topic": "pf_wage_ceiling", "as_of": "2026-08-15"},
            )
            assert r.status_code == 401
            now = datetime.now(tz=_UTC)
            token = jwt.encode(
                {"sub": "tester", "iat": now, "exp": now + timedelta(minutes=5)},
                settings.jwt_secret,
                algorithm="HS256",
            )
            ok = client.get(
                "/v1/statutory/resolve",
                params={"region": "IN", "topic": "pf_wage_ceiling", "as_of": "2026-08-15"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert ok.status_code == 200
    finally:
        if old_mode is None:
            os.environ.pop("PAYCE_AUTH_MODE", None)
        else:
            os.environ["PAYCE_AUTH_MODE"] = old_mode
        if old_secret is None:
            os.environ.pop("PAYCE_JWT_SECRET", None)
        else:
            os.environ["PAYCE_JWT_SECRET"] = old_secret
