"""Route tests for the WebAuthn passkey endpoints.

The crypto and DB-heavy pieces live in the service layer and are
covered separately; here we verify the route plumbing (auth decorators,
body validation, error envelope mapping) with the service calls
stubbed out.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import auth_passkey as route
from backend.core.exceptions import (
    PASSKEY_AUTH_FAILED,
    PASSKEY_REGISTRATION_FAILED,
    AppError,
)
from backend.data.models.users import User


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    email: str = "pat@example.test"


# ---------------------------------------------------------------------------
# /auth/passkey/register/start
# ---------------------------------------------------------------------------


def test_register_start_rejects_unauthenticated(client: FlaskClient) -> None:
    resp = client.post("/api/v1/auth/passkey/register/start")
    assert resp.status_code == 401


def test_register_start_returns_options_and_state(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start hands the caller public-key options and a signed state token."""
    client, _user, headers = authed_client
    challenge = MagicMock(options={"challenge": "abc"}, state="state-jwt")
    monkeypatch.setattr(
        route.auth_service,
        "passkey_registration_options",
        lambda _s, user: challenge,
    )
    resp = client.post("/api/v1/auth/passkey/register/start", headers=headers())
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["options"] == {"challenge": "abc"}
    assert data["state"] == "state-jwt"


# ---------------------------------------------------------------------------
# /auth/passkey/register/complete
# ---------------------------------------------------------------------------


def test_register_complete_rejects_unauthenticated(client: FlaskClient) -> None:
    resp = client.post(
        "/api/v1/auth/passkey/register/complete",
        json={"credential": {"id": "c"}, "state": "s"},
    )
    assert resp.status_code == 401


def test_register_complete_rejects_missing_credential(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _user, headers = authed_client
    resp = client.post(
        "/api/v1/auth/passkey/register/complete",
        headers=headers(),
        json={"state": "s"},
    )
    assert resp.status_code == 422


def test_register_complete_rejects_missing_state(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _user, headers = authed_client
    resp = client.post(
        "/api/v1/auth/passkey/register/complete",
        headers=headers(),
        json={"credential": {"id": "c"}},
    )
    assert resp.status_code == 422


def test_register_complete_rejects_non_string_name(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _user, headers = authed_client
    resp = client.post(
        "/api/v1/auth/passkey/register/complete",
        headers=headers(),
        json={"credential": {"id": "c"}, "state": "s", "name": 42},
    )
    assert resp.status_code == 422


def test_register_complete_happy_path(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, user, headers = authed_client
    called: dict[str, Any] = {}

    def fake_complete(_s: Any, **kwargs: Any) -> User:
        called.update(kwargs)
        return user

    monkeypatch.setattr(route.auth_service, "passkey_register_complete", fake_complete)
    resp = client.post(
        "/api/v1/auth/passkey/register/complete",
        headers=headers(),
        json={"credential": {"id": "c"}, "state": "s", "name": "iPhone"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["registered"] is True
    assert called["name"] == "iPhone"


def test_register_complete_surfaces_service_failure(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _user, headers = authed_client

    def boom(*_a: Any, **_k: Any) -> None:
        raise AppError(code=PASSKEY_REGISTRATION_FAILED, message="no", status_code=400)

    monkeypatch.setattr(route.auth_service, "passkey_register_complete", boom)
    resp = client.post(
        "/api/v1/auth/passkey/register/complete",
        headers=headers(),
        json={"credential": {"id": "c"}, "state": "s"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == PASSKEY_REGISTRATION_FAILED


# ---------------------------------------------------------------------------
# /auth/passkey/authenticate/start (public)
# ---------------------------------------------------------------------------


def test_authenticate_start_returns_options_and_state(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The authenticate-start endpoint is unauthenticated and always public."""
    challenge = MagicMock(options={"challenge": "xyz"}, state="state-jwt")
    monkeypatch.setattr(
        route.auth_service, "passkey_authentication_options", lambda: challenge
    )
    resp = client.post("/api/v1/auth/passkey/authenticate/start")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["options"] == {"challenge": "xyz"}
    assert data["state"] == "state-jwt"


# ---------------------------------------------------------------------------
# /auth/passkey/authenticate/complete
# ---------------------------------------------------------------------------


def test_authenticate_complete_rejects_missing_body(client: FlaskClient) -> None:
    resp = client.post("/api/v1/auth/passkey/authenticate/complete", json={})
    assert resp.status_code == 422


def test_authenticate_complete_happy_path(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid assertion returns a JWT and serialized user envelope."""
    user = _FakeUser()
    login = MagicMock(user=user, jwt="JWT-KEY")
    monkeypatch.setattr(
        route.auth_service,
        "passkey_authenticate_complete",
        lambda _s, credential, state: login,
    )
    monkeypatch.setattr(
        route.users_service, "serialize_user", lambda u: {"id": str(u.id)}
    )
    resp = client.post(
        "/api/v1/auth/passkey/authenticate/complete",
        json={"credential": {"id": "c"}, "state": "s"},
    )
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["token"] == "JWT-KEY"
    assert body["user"]["id"] == str(user.id)


def test_authenticate_complete_surfaces_service_failure(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise AppError(code=PASSKEY_AUTH_FAILED, message="nope", status_code=400)

    monkeypatch.setattr(route.auth_service, "passkey_authenticate_complete", boom)
    resp = client.post(
        "/api/v1/auth/passkey/authenticate/complete",
        json={"credential": {"id": "c"}, "state": "s"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == PASSKEY_AUTH_FAILED
