"""Route tests for the Apple OAuth endpoints."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import jwt
import pytest
from flask.testing import FlaskClient

from backend.api.v1 import auth_apple as route
from backend.core.config import get_settings
from backend.core.exceptions import APPLE_AUTH_FAILED, AppError


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    email: str = "pat@example.test"


def test_apple_start_returns_authorize_url_and_state(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start returns a consent URL plus a signed state token."""
    monkeypatch.setattr(
        route.auth_service,
        "apple_build_authorize_url",
        lambda state: f"https://appleid.apple.com/auth/authorize?state={state}",
    )
    resp = client.get("/api/v1/auth/apple/start")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["authorize_url"].startswith("https://appleid.apple.com/")
    claims = jwt.decode(
        data["state"],
        get_settings().jwt_secret_key,
        algorithms=["HS256"],
    )
    assert claims["purpose"] == "apple_oauth_state"


def _valid_state() -> str:
    """Mint a valid Apple state token via the module helper."""
    return route._issue_state_token()


def test_apple_complete_rejects_missing_code(client: FlaskClient) -> None:
    resp = client.post(
        "/api/v1/auth/apple/complete",
        json={"state": _valid_state()},
    )
    assert resp.status_code == 422


def test_apple_complete_rejects_missing_state(client: FlaskClient) -> None:
    resp = client.post("/api/v1/auth/apple/complete", json={"code": "abc"})
    assert resp.status_code == 422


def test_apple_complete_rejects_non_dict_user(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``user`` must be an object when provided."""
    resp = client.post(
        "/api/v1/auth/apple/complete",
        json={"code": "abc", "state": _valid_state(), "user": "Pat"},
    )
    assert resp.status_code == 422


def test_apple_complete_happy_path(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid code + user payload returns JWT + serialized user."""
    user = _FakeUser()
    login = MagicMock(user=user, jwt="JWT-AAA")
    monkeypatch.setattr(
        route.auth_service,
        "apple_complete",
        lambda _s, code, user_data: login,
    )
    monkeypatch.setattr(
        route.users_service, "serialize_user", lambda u: {"id": str(u.id)}
    )
    resp = client.post(
        "/api/v1/auth/apple/complete",
        json={
            "code": "abc",
            "state": _valid_state(),
            "user": {"name": {"firstName": "Pat"}},
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["token"] == "JWT-AAA"
    assert body["user"]["id"] == str(user.id)


def test_apple_complete_surfaces_service_failure(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service-layer APPLE_AUTH_FAILED bubbles through as HTTP 400."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise AppError(code=APPLE_AUTH_FAILED, message="down", status_code=400)

    monkeypatch.setattr(route.auth_service, "apple_complete", boom)
    resp = client.post(
        "/api/v1/auth/apple/complete",
        json={"code": "abc", "state": _valid_state()},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == APPLE_AUTH_FAILED
