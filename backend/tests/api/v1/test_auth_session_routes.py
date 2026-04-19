"""Route tests for ``/auth/me`` and ``/auth/logout``.

The login-method-agnostic session endpoints only require that the
``@require_auth`` decorator admits the caller; their behavior is
trivial otherwise, so these tests just confirm the decorator wiring
is in place and the response envelopes look right.

Doubles as the integration test surface for ``require_auth`` itself —
the decorator's negative paths (legacy HS256 tokens, expired RS256,
malformed UUID subjects) are easiest to assert through a real route.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient

from backend.api.v1 import auth_session as route
from backend.core import auth as auth_module
from backend.data.models.users import User
from backend.tests.conftest import (
    KNUCKLES_TEST_CLIENT_ID,
    KNUCKLES_TEST_URL,
    mint_knuckles_token,
)


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


def test_auth_me_rejects_legacy_hs256_token(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """Old HS256 ``issue_token`` outputs no longer pass ``require_auth``."""
    legacy = auth_module.issue_token(uuid.uuid4())
    monkeypatch.setattr(
        auth_module.users_repo,
        "get_user_by_id",
        lambda _s, _uid: MagicMock(),
    )
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {legacy}"})
    assert resp.status_code == 401


def test_auth_me_rejects_expired_knuckles_token(
    client: FlaskClient,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """An RS256 token with a past ``exp`` is rejected."""
    token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=uuid.uuid4(),
        exp_offset=-30,
    )
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_auth_me_rejects_token_with_non_uuid_subject(
    client: FlaskClient,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """A valid signature but a non-UUID ``sub`` rejects with 401."""
    import time as _time

    now = int(_time.time())
    token = jwt.encode(
        {
            "iss": KNUCKLES_TEST_URL,
            "sub": "not-a-uuid",
            "aud": KNUCKLES_TEST_CLIENT_ID,
            "iat": now,
            "exp": now + 600,
        },
        knuckles_test_key,
        algorithm="RS256",
        headers={"kid": stub_knuckles_jwks},
    )
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_auth_me_rejects_when_user_row_missing(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """Knuckles authenticated the user but Greenroom has no row → 401."""
    monkeypatch.setattr(auth_module.users_repo, "get_user_by_id", lambda _s, _uid: None)
    token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=uuid.uuid4(),
    )
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
