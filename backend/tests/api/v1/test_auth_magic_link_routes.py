"""Route tests for the magic-link sign-in endpoints.

The service layer already has its own unit tests
(:mod:`backend.tests.services.test_auth_magic_link`); these tests
exercise the thin HTTP shell — request validation, status codes, and
the response envelope.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import auth_magic_link as route
from backend.core.exceptions import (
    MAGIC_LINK_ALREADY_USED,
    MAGIC_LINK_EXPIRED,
    MAGIC_LINK_INVALID,
    AppError,
)


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    email: str = "pat@example.test"
    display_name: str | None = "Pat"
    avatar_url: str | None = None


# ---------------------------------------------------------------------------
# POST /auth/magic-link/request
# ---------------------------------------------------------------------------


def test_request_rejects_non_json_body(client: FlaskClient) -> None:
    """An empty body fails validation before the service runs."""
    resp = client.post(
        "/api/v1/auth/magic-link/request",
        data="",
        content_type="text/plain",
    )
    assert resp.status_code == 422


def test_request_rejects_missing_email(client: FlaskClient) -> None:
    """``email`` is required in the JSON body."""
    resp = client.post("/api/v1/auth/magic-link/request", json={})
    assert resp.status_code == 422


def test_request_accepts_valid_email(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path returns HTTP 202 and never leaks the raw token."""
    delivery = MagicMock(
        raw_token="SECRET_TOKEN",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    generate = MagicMock(return_value=delivery)
    monkeypatch.setattr(route.auth_service, "generate_magic_link", generate)

    resp = client.post(
        "/api/v1/auth/magic-link/request",
        json={"email": "Pat@Example.TEST"},
    )
    assert resp.status_code == 202
    body = resp.get_json()
    # The response must never contain the raw token — that only goes out
    # in the email itself.
    assert "SECRET_TOKEN" not in resp.get_data(as_text=True)
    assert body["data"]["email_sent"] is True
    generate.assert_called_once()
    assert generate.call_args.kwargs["email"] == "Pat@Example.TEST"


def test_request_swallows_delivery_failure_to_prevent_enumeration(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SendGrid failure must not leak which addresses are registered.

    The response shape stays the same regardless of whether the email
    actually went out — a 202 with ``email_sent: true``. The service
    logs the underlying failure.
    """
    from backend.core.exceptions import EMAIL_DELIVERY_FAILED

    def boom(*_a: Any, **_k: Any) -> None:
        raise AppError(
            code=EMAIL_DELIVERY_FAILED,
            message="down",
            status_code=502,
        )

    monkeypatch.setattr(route.auth_service, "generate_magic_link", boom)

    resp = client.post(
        "/api/v1/auth/magic-link/request",
        json={"email": "pat@example.test"},
    )
    assert resp.status_code == 202
    assert resp.get_json()["data"]["email_sent"] is True


# ---------------------------------------------------------------------------
# POST /auth/magic-link/verify
# ---------------------------------------------------------------------------


def test_verify_rejects_missing_token(client: FlaskClient) -> None:
    """``token`` is required in the JSON body."""
    resp = client.post("/api/v1/auth/magic-link/verify", json={})
    assert resp.status_code == 422


def test_verify_happy_path_returns_token_and_user(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid token returns a session JWT and the serialized user."""
    user = _FakeUser()
    verification = MagicMock(user=user, jwt="JWT-123")
    monkeypatch.setattr(
        route.auth_service,
        "verify_magic_link",
        lambda _s, token: verification,
    )
    monkeypatch.setattr(
        route.users_service, "serialize_user", lambda u: {"id": str(u.id)}
    )

    resp = client.post(
        "/api/v1/auth/magic-link/verify",
        json={"token": "raw"},
    )
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["token"] == "JWT-123"
    assert body["user"]["id"] == str(user.id)


@pytest.mark.parametrize(
    "code",
    [MAGIC_LINK_INVALID, MAGIC_LINK_EXPIRED, MAGIC_LINK_ALREADY_USED],
)
def test_verify_surfaces_service_error_codes(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    """Each service error code maps to HTTP 400 with the code in the body."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise AppError(code=code, message="nope", status_code=400)

    monkeypatch.setattr(route.auth_service, "verify_magic_link", boom)

    resp = client.post(
        "/api/v1/auth/magic-link/verify",
        json={"token": "raw"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == code
