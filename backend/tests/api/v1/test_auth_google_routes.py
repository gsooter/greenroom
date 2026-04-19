"""Route tests for the Google OAuth endpoints."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import jwt
import pytest
from flask.testing import FlaskClient

from backend.api.v1 import auth_google as route
from backend.core.config import get_settings
from backend.core.exceptions import GOOGLE_AUTH_FAILED, AppError


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    email: str = "pat@example.test"


# ---------------------------------------------------------------------------
# /auth/google/start
# ---------------------------------------------------------------------------


def test_google_start_returns_authorize_url_and_state(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start returns a consent URL with an embedded signed state token."""
    monkeypatch.setattr(
        route.auth_service,
        "google_build_authorize_url",
        lambda state: f"https://accounts.google.com/o/oauth2/v2/auth?state={state}",
    )
    resp = client.get("/api/v1/auth/google/start")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["authorize_url"].startswith("https://accounts.google.com/")
    claims = jwt.decode(
        data["state"],
        get_settings().jwt_secret_key,
        algorithms=["HS256"],
    )
    assert claims["purpose"] == "google_oauth_state"


# ---------------------------------------------------------------------------
# /auth/google/complete
# ---------------------------------------------------------------------------


def _valid_state() -> str:
    """Mint a valid state token with the module's own helper."""
    return route._issue_state_token()


def test_google_complete_rejects_missing_code(client: FlaskClient) -> None:
    resp = client.post(
        "/api/v1/auth/google/complete",
        json={"state": _valid_state()},
    )
    assert resp.status_code == 422


def test_google_complete_rejects_missing_state(client: FlaskClient) -> None:
    resp = client.post("/api/v1/auth/google/complete", json={"code": "abc"})
    assert resp.status_code == 422


def test_google_complete_rejects_wrong_purpose_state(client: FlaskClient) -> None:
    """A well-signed token with the wrong purpose → GOOGLE_AUTH_FAILED."""
    now = datetime.now(UTC)
    wrong = jwt.encode(
        {
            "purpose": "something_else",
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        get_settings().jwt_secret_key,
        algorithm="HS256",
    )
    resp = client.post(
        "/api/v1/auth/google/complete",
        json={"code": "abc", "state": wrong},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == GOOGLE_AUTH_FAILED


def test_google_complete_happy_path(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid code/state pair returns a JWT and serialized user."""
    user = _FakeUser()
    login = MagicMock(user=user, jwt="JWT-321")
    monkeypatch.setattr(route.auth_service, "google_complete", lambda _s, code: login)
    monkeypatch.setattr(
        route.users_service, "serialize_user", lambda u: {"id": str(u.id)}
    )
    resp = client.post(
        "/api/v1/auth/google/complete",
        json={"code": "abc", "state": _valid_state()},
    )
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["token"] == "JWT-321"
    assert body["user"]["id"] == str(user.id)


def test_google_complete_surfaces_service_failure(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service-layer errors bubble through the error handler to HTTP 400."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise AppError(code=GOOGLE_AUTH_FAILED, message="nope", status_code=400)

    monkeypatch.setattr(route.auth_service, "google_complete", boom)

    resp = client.post(
        "/api/v1/auth/google/complete",
        json={"code": "abc", "state": _valid_state()},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == GOOGLE_AUTH_FAILED
