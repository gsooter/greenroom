"""Route tests for ``/auth/me`` and ``/auth/logout``.

The login-method-agnostic session endpoints only require that the
``@require_auth`` decorator admits the caller; their behavior is
trivial otherwise, so these tests just confirm the decorator wiring
is in place and the response envelopes look right.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import auth_session as route
from backend.data.models.users import User


def test_auth_me_rejects_unauthenticated(client: FlaskClient) -> None:
    """No Authorization header → 401."""
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_auth_me_returns_serialized_user(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authenticated calls get the serialized user back."""
    client, user, headers = authed_client
    monkeypatch.setattr(
        route.users_service,
        "serialize_user",
        lambda u: {"id": str(u.id), "email": u.email},
    )
    resp = client.get("/api/v1/auth/me", headers=headers())
    assert resp.status_code == 200
    assert resp.get_json()["data"]["email"] == user.email


def test_auth_logout_rejects_unauthenticated(client: FlaskClient) -> None:
    """Logout requires a valid token like every other session endpoint."""
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 401


def test_auth_logout_returns_204(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    """Authenticated logout returns an empty 204 response."""
    client, _user, headers = authed_client
    resp = client.post("/api/v1/auth/logout", headers=headers())
    assert resp.status_code == 204
